"""
F6.4 unit tests (docs/F6.4_DESIGN.md section 21.1, U1-U10). No database: loader rendering over fake rows, the
envelopes and their hashes, the codec round trip, selection and the partition fingerprint, the pinned Decimal
context, the static rules migration 0015 must satisfy, SQL / Python vocabulary parity, F6.3 untouched, frozen pins,
and the section 11.6 decomposition with its Python mirror refusing every tamper class of P18.
"""
import ast
import copy
import dataclasses
import glob
import json
import os
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from f63_factories import Doc, configuration  # noqa: E402
from f64_scenarios import scenario_docs, tamper_docs, with_ids  # noqa: E402
from f64_support import pad  # noqa: E402
from f64_tampers import MIRROR, RECORD_TAMPERS, VALIDATION_TAMPERS, candidate_of_other_run, exact  # noqa: E402
from worker import financial_candidates as f5  # noqa: E402
from worker import financial_validation as fv  # noqa: E402
from worker.financial_truth import admission, canonical, identity, observations, reconciliation, versions  # noqa: E402
from worker.financial_truth_store import (F6_DECIMAL_CONTEXT, F6_LOCK_KEY, HELPER_FUNCTIONS, STORE_VERSION,  # noqa: E402
                                          codec, jobs, loader, preflight, selection, writer)
from worker.ops import migrate as mig  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
MIG0015 = os.path.join(REPO, "supabase", "migrations", "0015_financial_truth_persistence.sql")
COLOMBO = timezone(timedelta(hours=5, minutes=30))
D = Decimal


def sql0015():
    return open(MIG0015, encoding="utf-8").read()


@pytest.fixture(scope="module")
def world():
    docs = with_ids(scenario_docs())
    vrs = {k: d.validate() for k, d in docs.items()}
    sos = {k: observations.build(vr) for k, vr in vrs.items()}
    cfg = configuration(*docs.values())
    batch = reconciliation.reconcile([vr.f5_run for vr in vrs.values()], [o for s in sos.values() for o in s], cfg)
    return {"docs": docs, "vrs": vrs, "sos": sos, "cfg": cfg, "batch": batch}


def rows_of(world, key):
    vr, sos = world["vrs"][key], world["sos"][key]
    rows = codec.validation_rows(vr, sos, store_version=STORE_VERSION, code_revision=None, job_id=None,
                                 started_at=None, finished_at=None)
    reported = {c["id"]: codec.candidate_reported_json(c) for c in world["docs"][key].result()["candidates"]}
    return rows, reported


# ------------------------------------------------------------------------------------------------ U1 loader

class FakeCursor:
    """Answers the loader's queries from padded factory rows (dispatch on the FROM clause)."""

    def __init__(self, tables):
        self.tables, self.description, self._rows = tables, None, []

    def execute(self, sql, args=None):
        if sql.startswith("set "):
            return
        for key in ("financial_fact_candidates fc", "financial_statement_columns c", "financial_statement_rows r",
                    "financial_statement_extracts where", "financial_extraction_runs where"):
            if key in sql:
                rows = self.tables[key]
                break
        else:
            raise AssertionError(sql)
        names = list(rows[0]) if rows else []
        self.description = [(n,) for n in names]
        self._rows = [tuple(r[n] for n in names) for r in rows]

    def fetchall(self):
        return self._rows


def fake_tables(doc, run_id="0b7e0d8e-1111-4c1c-9c53-000000000001"):
    res = pad(doc.result())
    run = dict(res["run"], id=run_id, classification_id="5f0c8a1e-2222-4d2d-8d64-000000000002",
               content_sha256="ab" * 32, recorded_at=datetime(2026, 5, 16, 5, 30, tzinfo=COLOMBO),
               **{k: None for k in loader.TIMESTAMP_FIELDS})
    run["uploaded_at"] = datetime(2026, 5, 15, 9, 30, 0, 123456, tzinfo=COLOMBO)       # as psycopg2 may return it
    run["path_epoch_ms"] = 1762944976777
    cands = [dict(c, id=100 + i) for i, c in enumerate(res["candidates"])]
    return {"financial_extraction_runs where": [run],
            "financial_statement_extracts where": res["statements"], "financial_statement_columns c": res["columns"],
            "financial_statement_rows r": res["rows"], "financial_fact_candidates fc": cands}


def test_u1_loader_renders_timestamps_uuids_and_decimals_as_f5_does():
    doc = Doc().one("1,234.50", stmt_scale=1)
    cur = FakeCursor(fake_tables(doc))
    result, run = loader.f5_result(cur, "x")
    ts = result["run"]["timestamps"]
    assert ts["uploaded_at"] == f5._ts(run["uploaded_at"]) == "2026-05-15T04:00:00.123456+00:00"
    assert ts["path_epoch_ms"] == 1762944976777 and ts["authorized_at"] is None
    for when in (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 12, tzinfo=COLOMBO),
                 datetime(2026, 1, 1, 23, 59, 59, 999999, tzinfo=timezone(timedelta(hours=-11)))):
        assert loader._ts(when) == f5._ts(when)
    with pytest.raises(loader.LoaderError):
        loader._ts(datetime(2026, 1, 1))                           # naive: never assumed
    ref = loader.f5_run_ref(cur, "x", result, run)
    assert ref.f5_run_id == run["id"] and ref.classification_id == run["classification_id"]
    assert ref.recorded_at == "2026-05-16T00:00:00+00:00" and ref.content_sha256 == "ab" * 32
    (cand,) = result["candidates"]
    assert cand["id"] == 100 and cand["parsed_value"] == D("1234.50") and str(cand["parsed_value"]) == "1234.50"
    vr = admission.validate_run(result, f5_run=ref, issuer_link=doc.issuer_link(), uploaded_at=run["uploaded_at"],
                                document=doc.document())
    (c,) = vr.candidates
    assert c.admission.admitted and c.candidate_id == 100 and c.validation.value.normalized_value == D("1234.50")


def test_u1_loader_refuses_a_candidate_without_an_id():
    doc = Doc().one()
    tables = fake_tables(doc)
    tables["financial_fact_candidates fc"][0]["id"] = None
    with pytest.raises(loader.LoaderError):
        loader.f5_result(FakeCursor(tables), "x")


# ------------------------------------------------------------------------------------------------ U2 envelopes

def test_u2_envelopes_hash_exactly_to_f63_hashes(world):
    for vr in world["vrs"].values():
        assert codec.sha256_hex(codec.e1(vr)) == vr.output_hash
        for r in vr.candidates:
            assert codec.sha256_hex(codec.e2(r)) == r.output_hash
    for sos in world["sos"].values():
        for so in sos:
            assert codec.sha256_hex(codec.e3(so)) == so.output_hash
    for r in world["batch"].results:
        assert codec.sha256_hex(codec.e4(r)) == r.output_hash
    assert codec.sha256_hex(codec.e5(world["cfg"])) == world["cfg"].configuration_id
    assert codec.sha256_hex(codec.e6(world["batch"])) == world["batch"].output_hash


def test_u2_checked_envelope_refuses_a_wrong_hash_or_a_float():
    with pytest.raises(codec.CodecError):
        codec.checked_envelope('{"a":1}', "0" * 64, "E?")
    text = '{"a":1.5}'
    with pytest.raises(codec.CodecError):
        codec.checked_envelope(text, codec.sha256_hex(text), "E?")


# ------------------------------------------------------------------------------------------------ U3 codec round trip

def test_u3_codec_round_trips_every_so_result_and_configuration(world):
    shapes = set()
    for sos in world["sos"].values():
        for so in sos:
            back = codec.decode_source_observation(codec.e3(so), so.output_hash)
            assert back == so and canonical.canonical_json(back) == canonical.canonical_json(so)
            shapes.add((so.observation_status, so.value_kind, so.representative_member is None))
    assert ("consistent", "nil", False) in shapes and ("internally_conflicting", "numeric", True) in shapes
    states = set()
    for r in world["batch"].results:
        assert codec.decode_result(codec.e4(r), r.output_hash) == r
        states.add(r.state)
    assert states == set(reconciliation.STATES)
    ambiguous = [r for r in world["batch"].results if "representative_ambiguous" in r.annotations]
    assert ambiguous and all(codec.decode_result(codec.e4(r), r.output_hash) == r for r in ambiguous)
    assert codec.decode_configuration(codec.e5(world["cfg"])) == world["cfg"]


def test_u3_decoder_refuses_unknown_or_missing_fields(world):
    so = world["sos"]["d1"][0]
    d = json.loads(codec.e3(so))
    d["extra"] = 1
    with pytest.raises(codec.CodecError):
        codec.decode(observations.SourceObservation, json.dumps(d))


# ------------------------------------------------------------------------------------------------ U4 selection

def test_u4_fingerprint_is_stable_under_input_order_and_sensitive_to_content(world):
    sos = [o for s in world["sos"].values() for o in s]
    runs = [vr.f5_run for vr in world["vrs"].values()]
    sel = reconciliation.select_runs(runs, world["cfg"])
    base = selection.fingerprint(world["cfg"].configuration_id, "i", sel, sos)
    for seed in range(5):
        r = random.Random(seed)
        shuffled_runs, shuffled_sos = runs[:], sos[:]
        r.shuffle(shuffled_runs)
        r.shuffle(shuffled_sos)
        assert selection.fingerprint(world["cfg"].configuration_id, "i",
                                     reconciliation.select_runs(shuffled_runs, world["cfg"]), shuffled_sos) == base
    assert selection.fingerprint(world["cfg"].configuration_id, "i", sel, sos[1:]) != base
    assert selection.fingerprint(world["cfg"].configuration_id, "j", sel, sos) != base


def test_u4_current_input_set_view_and_constants():
    sql = sql0015()
    assert "order by l.id desc limit 1" in sql and "vr.publication_uploaded_at is not distinct from f.uploaded_at" in sql
    assert STORE_VERSION == "f6.store.1" and F6_LOCK_KEY == 4_346_836_117_002_313
    used = set()
    for p in glob.glob(os.path.join(REPO, "worker", "**", "*.py"), recursive=True):
        used |= set(re.findall(r"4_?346_?836_?117_?002_?31\d", open(p, encoding="utf-8").read()))
    assert {k.replace("_", "") for k in used} == {"4346836117002311", "4346836117002312", "4346836117002313"}


# ------------------------------------------------------------------------------------------------ U5 Decimal context

def test_u5_pinned_decimal_context_reproduces_the_default_results():
    """F6.1's compare_values takes abs() in the AMBIENT context (inherited, not fixed in F6.4); F6.4 pins the default
    context around every F6.3 call, so a caller-lowered precision cannot change a stored result."""
    def run():
        d = Doc()
        st = d.statement(scale=1)
        ci = d.column(st)
        for label, raw in (("Turnover", "1,234"), ("Revenue", "1,234,500.06")):
            d.value(st, d.row(st, label), ci, raw)
        return observations.build(d.validate())[0]
    default = run()
    with localcontext() as ctx:
        ctx.prec = 3
        unpinned = run()
        with localcontext(F6_DECIMAL_CONTEXT):
            pinned = run()
    assert unpinned.output_hash != default.output_hash         # the inherited caveat is real ...
    assert pinned.output_hash == default.output_hash           # ... and pinning removes it
    assert (F6_DECIMAL_CONTEXT.prec, F6_DECIMAL_CONTEXT.rounding, F6_DECIMAL_CONTEXT.Emin, F6_DECIMAL_CONTEXT.Emax,
            F6_DECIMAL_CONTEXT.capitals, F6_DECIMAL_CONTEXT.clamp) == (28, "ROUND_HALF_EVEN", -999999, 999999, 1, 0)
    import decimal
    assert {t for t, on in F6_DECIMAL_CONTEXT.traps.items() if on} == {decimal.InvalidOperation,
                                                                       decimal.DivisionByZero, decimal.Overflow}


def test_u5_every_f63_call_in_the_jobs_runs_inside_the_pinned_context():
    src = open(os.path.join(REPO, "worker", "financial_truth_store", "jobs.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    calls = {"validate_run", "build", "select_runs", "reconcile"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in calls \
                and isinstance(node.func.value, ast.Name) and node.func.value.id in ("admission", "observations",
                                                                                   "reconciliation"):
            parents = [p for p in ast.walk(tree) if isinstance(p, ast.With)
                       and any("F6_DECIMAL_CONTEXT" in ast.unparse(i.context_expr) for i in p.items)
                       and any(n is node for n in ast.walk(p))]
            assert parents, f"{node.func.value.id}.{node.func.attr} at line {node.lineno} is not pinned"


# ------------------------------------------------------------------------------------------------ U6 static rules

def test_u6_migration_0015_static_rules():
    s = sql0015().lower()
    assert "bytea" not in s                                                           # F2 guard
    assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", s)
    assert not re.search(r"^\s*(alter|drop)\b", s, re.M) and "alter default privileges" not in s
    assert "security definer" not in s and "cse.lk" not in s and "row level security" not in s
    assert not re.search(r"grant[^;]*\b(update|delete|truncate)\b", s)
    assert not re.search(r"numeric\s*\(", s) and not re.search(r"\b(real|float\d*|double precision)\b", s)
    tables = re.findall(r"create table (\w+)", s)
    assert len(tables) == 16
    for t in tables:
        assert re.search(rf"before update or delete on {t}\s+for each row execute function f5_reject_mutation", s), t
        assert re.search(rf"before truncate on {t}\s+for each statement execute function f5_reject_mutation", s), t
    children = {"financial_candidate_validations": "candidate_ordinal", "financial_op1_records": "op1_ordinal",
                "financial_so_members": "member_ordinal", "financial_so_comparisons": "comparison_ordinal",
                "financial_reconciliation_inputs": "observation_ordinal",
                "financial_reconciliation_comparisons": "comparison_ordinal",
                "financial_reconciliation_batch_results": "result_ordinal"}
    for t, col in children.items():
        body = re.search(rf"create table {t} \((.*?)\n\);", s, re.S).group(1)
        assert re.search(rf"unique \(\w+, {col}\)", body), t
    for col in ("op1_json", "member_json", "comparison_json", "observation_json"):
        assert re.search(rf"{col} ~ '\^\[ -~\]\+\$'", s), col
    seals = set(re.findall(r"after insert on (\w+)\s+deferrable initially deferred for each row execute function "
                           r"f6_child_seal", s))
    assert seals == {"financial_candidate_validations", "financial_op1_records", "financial_source_observations",
                     "financial_so_members", "financial_so_comparisons", "financial_reconciliation_records",
                     "financial_reconciliation_inputs", "financial_reconciliation_comparisons",
                     "financial_reconciliation_batch_results"}
    assert all(ch == "\n" or " " <= ch <= "~" for ch in sql0015())


def test_u6_preflight_lists_every_trigger_and_helper():
    s = sql0015()
    assert set(re.findall(r"create (?:constraint )?trigger (\w+)", s)) == {n for v in preflight.TRIGGERS.values() for n in v}
    granted = re.search(r"grant execute on function (.*?)\n\s+to cse_worker;", s, re.S).group(1)
    for fn in HELPER_FUNCTIONS:
        assert fn.split("(")[0] in granted
    for fn in preflight.TRIGGER_FUNCTIONS:
        assert fn.split("(")[0] + "(" not in granted


# ------------------------------------------------------------------------------------------------ U7 parity

def _in_list(sql, constraint):
    body = re.search(rf"constraint {constraint} check \((.*?)\)(?:,\n|\n\);)", sql, re.S).group(1)
    return body


def test_u7_sql_vocabularies_equal_f63_constants():
    s = sql0015()
    ann = re.search(r"annotations <@ array\['agreement_within_precision_only',\s*'differs(.*?)\]::text\[\]\)", s, re.S)
    full = set(re.findall(r"'(\w+)'", "'agreement_within_precision_only', 'differs" + ann.group(1)))
    assert full == set(reconciliation.ANNOTATIONS)
    states = re.search(r"chk_frr_state check \(state in \(([^)]*)\)", s).group(1)
    assert set(re.findall(r"'(\w+)'", states)) == set(reconciliation.STATES)
    so_ann = re.search(r"annotations <@ array\[('agreement_within_precision_only', 'multiple_roles', "
                       r"'representative_ambiguous')\]", s)
    assert set(re.findall(r"'(\w+)'", so_ann.group(1))) == set(observations.SO_ANNOTATIONS)
    assert "scope in ('group', 'company', 'bank', 'unlabelled')" in s and set(identity.SCOPES) == {
        "group", "company", "bank", "unlabelled"}
    assert "operations in ('total_or_unstated', 'continuing', 'discontinued')" in s and set(identity.OPERATIONS) == {
        "total_or_unstated", "continuing", "discontinued"}
    assert "maturity in ('current', 'non_current', 'not_applicable')" in s
    assert set(identity.MATURITIES) | {identity.MATURITY_NOT_APPLICABLE} == {"current", "non_current",
                                                                               "not_applicable"}
    assert "(concept_key = 'interest_bearing_borrowings')" in s and fv.BORROWING_CONCEPTS == (
        "interest_bearing_borrowings",)
    assert set(reconciliation.VALUE_KINDS) == {"numeric", "nil"}
    v = versions.IMPLEMENTED
    for pattern, value in ((r"f6\.validation\.[0-9]+", v.validation_version), (r"f6\.inputs\.[0-9]+", v.input_policy_version),
                           (r"f6\.op1\.partition\.[0-9]+", v.op1_version), (r"f6\.admission\.[0-9]+", v.admission_version),
                           (r"f6\.identity\.[0-9]+", v.identity_version),
                           (r"f6\.reconciliation\.[0-9]+", versions.RECONCILIATION_VERSION)):
        assert re.fullmatch(pattern, value)
    assert "identity_version = 'f6.identity.1'" in s and v.identity_version == "f6.identity.1"


# ------------------------------------------------------------------------------------------------ U8 F6.3 untouched

def test_u8_f63_untouched_and_f64_imports_no_network_or_cse_module():
    files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(REPO, "worker", "financial_truth", "*.py")))
    assert len(files) == 10 and "financial_truth_store" not in files
    forbidden = re.compile(r"^(requests|urllib|http|socket|aiohttp|ftplib|smtplib|webbrowser)(\.|$)|cse_client|"
                           r"market_capture\.(capture|transport|client|http)|document_retrieval|pdf")
    for p in glob.glob(os.path.join(REPO, "worker", "financial_truth_store", "*.py")):
        for node in ast.walk(ast.parse(open(p, encoding="utf-8").read())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                assert not forbidden.search(n), f"{os.path.basename(p)} imports {n}"
        src = open(p, encoding="utf-8").read()
        assert "float(" not in src and "cse.lk" not in src.lower()


# ------------------------------------------------------------------------------------------------ U9 frozen pins

FROZEN = {
    "supabase/migrations/0001_phase1_data_foundation.sql": "51f2476f1a3afab8d172dca788e07af5d2b179d7e1b6ad9f7eecd8ca157ac02c",
    "supabase/migrations/0002_add_open_price.sql": "78d2e2cabb68cda0b9feab9260bace879bd78f0ddfc6928616f1e61a3f6429ea",
    "supabase/migrations/0003_eod_observation_completeness.sql": "2e94c091869df3a69a50c1f2bec3692d840b238285f8a11be1fb9a76d5a2a428",
    "supabase/migrations/0004_report_filings.sql": "05e9fb36329c8f823ed84f874ba528de3291917076ec6f43e8c92a3b1b98d72f",
    "supabase/migrations/0005_report_classification.sql": "2b035d8dba6a9e7e8ec8321bb44b2eb906e239435688c6e09594612c46cdddcc",
    "supabase/migrations/0007_issuers.sql": "68533d33e009bb1b18bda424db4bb86692a1afbe9240a8f6281f9a154d633b1b",
    "supabase/migrations/0008_financial_candidates.sql": "53aec0f040eb017cd1b10bcec3cff2ade645a3a3756988da7ee3b77aad7fbdb2",
    "supabase/migrations/0009_local_security_boundary.sql": "f573d327d55468a839f75ec874714e31ea9bb36dbf66ce0a0c5621274049077f",
    "supabase/migrations/0010_append_only_source_evidence.sql": "f4fdc0e7bcac16c4babe12a79ea99cd33204eea073dc90247616aaa67764703d",
    "supabase/migrations/0011_ops_backup_ledger.sql": "401dfbb27a1c703bf6517904de1ef8b24d6223170273d652893e77f54dfbb7fa",
    "supabase/migrations/0012_market_capture_archive.sql": "164c3c8e19019f822f2e18aca251d6da4e2883bf03f1346232f9446ee1ada135",
    "supabase/migrations/0013_market_capture_owner_acknowledgement.sql": "9d3e0092db911b108567c2dfca5c7b6c5eb496b5d256e6c5555a1840f350e812",
    "supabase/migrations/0014_market_capture_scheduler.sql": "1a616c45ba043da3c9f7f4cd287ea02a99b96ab5f0b373bea3ddb9ca2677d65b",
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
    "worker/financial_validation.py": "1e5f649016cff5ad8bee6fc89ad88d5783b4f165a020f24f82d37618669fe5d2",
    "docs/F6.2_DESIGN.md": "4c97c6a816fe8ccd55fed38b44e260434f78f11b2fced527fc83d879ef07514c",
    "docs/F6.3_IMPLEMENTATION.md": "f6a59b723c21e99171af2ff03a15464e0cbb8ef1362968a5253b8a0f8eb21db0",
    "worker/scheduler/__init__.py": "cd47b15b5798ffbc9e8482dfaa26d2a3d29d835158acbfbf6a9c3b1fdca5bf81",
    "worker/scheduler/__main__.py": "13a1a5b340cdcfc1902b62be90e508c7c71886000d5bf087e7854aadf09fb35e",
    "worker/scheduler/cli.py": "fab7dd435ea4b5b63df2fc905c754a7fa4703bb9d6068ddc1ba4bc55670a72f4",
    "worker/scheduler/planner.py": "e1c5b131157b8e1241a4d54c908baa887d47d00589d46b5f675978dc20cd63cd",
    "worker/scheduler/preflight.py": "29e7575487e4cffccd0fc52e4409e23d9978176ccbb1784321c060612a200ca3",
    "worker/scheduler/schedule.py": "824f16d6792d1a2df66114feb7b320e1510b9346f4113c3fadd70543f9d2a3f5",
    "worker/scheduler/store.py": "f1a818f7c0615c877752b38084f47b789048522b85fc897231a01020888d3a81",
    "worker/scheduler/wakeup.py": "b2e31664b1fb03d39778ab81ace7157b2a5fbadfcf8d556f2d749ac2bc609452",
}


def test_u9_frozen_files_are_unchanged():
    for rel, sha in FROZEN.items():
        assert mig.file_sha256(os.path.join(REPO, *rel.split("/"))) == sha, f"frozen file {rel} changed"


def test_u9_0015_directly_follows_0014_and_0006_stays_unused():
    names = [m.filename for m in mig.discover(os.path.join(REPO, "supabase", "migrations"))]
    i = names.index("0014_market_capture_scheduler.sql")
    assert names[i + 1] == "0015_financial_truth_persistence.sql"
    assert not any(n.startswith("0006_") for n in names)


# ------------------------------------------------------------------------------------------------ U10 decomposition

def test_u10_every_element_is_its_exact_text_inside_the_parent_envelope(world):
    for vr in world["vrs"].values():
        e1 = codec.e1(vr)
        for o in vr.op1:
            assert canonical.canonical_json(o) in e1
    for sos in world["sos"].values():
        for so in sos:
            e3 = codec.e3(so)
            for m in so.members:
                assert canonical.canonical_json(m) in e3
            for c in so.comparisons:
                assert canonical.canonical_json(c) in e3
    for r in world["batch"].results:
        e4 = codec.e4(r)
        assert all(canonical.canonical_json(o) in e4 for o in r.observations)
        assert all(canonical.canonical_json(c) in e4 for c in r.comparisons)


def test_u10_decompositions_reconstruct_and_satisfy_every_rule(world):
    shapes = {"members>1": 0, "pairs": 0, "cross": 0, "op1": 0}
    for key in world["vrs"]:
        rows, reported = rows_of(world, key)
        assert codec.check_validation(rows, reported) == []
        for part in ("T1", "T2", "T3", "T5", "T6", "T7"):
            for r in ([rows[part]] if part == "T1" else rows[part]):
                for col, v in r.items():
                    if col.endswith("_json") and v:
                        codec.integers_only(json.loads(v), col)
        shapes["members>1"] += sum(1 for s in rows["T5"] if s["member_count"] > 1)
        shapes["pairs"] += len(rows["T7"])
        shapes["op1"] += len(rows["T3"])
    so_by_key = {o.so_key: o for s in world["sos"].values() for o in s}
    for r in world["batch"].results:
        t13, t14, t15 = codec.record_rows(r, so_by_key)
        shapes["cross"] += len(t15)
        assert [(c["a_observation_ordinal"], c["b_observation_ordinal"], c["a_member_ordinal"], c["b_member_ordinal"])
                for c in t15] == sorted((c["a_observation_ordinal"], c["b_observation_ordinal"], c["a_member_ordinal"],
                                         c["b_member_ordinal"]) for c in t15)
    assert all(shapes.values()), shapes


def _record_env(world, predicate):
    so_by_key = {o.so_key: o for s in world["sos"].values() for o in s}
    for r in world["batch"].results:
        if predicate(r):
            t13, t14, t15 = codec.record_rows(r, so_by_key)
            sos = {}
            for o in r.observations:
                so = so_by_key[o.so_key]
                vr_key = so.validation_run_key
                key = next(k for k, vr in world["vrs"].items() if vr.key == vr_key)
                rows, _ = rows_of(world, key)
                s_row = next(s for s in rows["T5"] if s["so_key"] == so.so_key)
                sos[so.so_key] = {"row": s_row, "members": {m["candidate_validation_key"]: m for m in rows["T6"]
                                                            if m["so_key"] == so.so_key}}
            f = next(f for k in world["vrs"] for f in rows_of(world, k)[0]["T4"] if f["ef_key"] == r.ef_key)
            return t13, t14, t15, f, sos
    raise AssertionError("no such record")


TAMPER_VALIDATION = [
    ("decimal re-scaled", lambda d: d["T6"][0].update(normalized_value=Decimal(str(d["T6"][0]["normalized_value"]) + "0"))),
    ("value changed", lambda d: d["T5"][0].update(interval_low=d["T5"][0]["interval_low"] - 1)),
    ("half-unit changed", lambda d: d["T6"][0].update(half_unit=Decimal("5"))),
    ("tolerance changed", lambda d: d["T7"][0].update(tolerance=d["T7"][0]["tolerance"] + 1)),
    ("difference changed", lambda d: d["T7"][0].update(abs_difference=d["T7"][0]["abs_difference"] + 1)),
    ("sign-only flipped", lambda d: d["T7"][0].update(sign_only=not d["T7"][0]["sign_only"])),
    ("role changed", lambda d: d["T6"][0].update(role="current" if d["T6"][0]["role"] == "comparative"
                                                  else "comparative")),
    ("reason changed", lambda d: d["T2"][-1].update(admission_reasons=["validation_ineligible", "x"])),
    ("annotation changed", lambda d: d["T5"][0].update(annotations=[] if d["T5"][0]["annotations"]
                                                        else ["multiple_roles"])),
    ("reported changed", lambda d: d["T5"][0].update(reported_raw_value="1,235.2")),
    ("wrong key", lambda d: d["T2"][0].update(candidate_validation_key="0" * 64)),
    ("missing child", lambda d: d["T7"].pop()),
    ("extra child", lambda d: d["T6"].append(dict(d["T6"][0], member_ordinal=2))),
    ("duplicate child", lambda d: d["T2"].append(dict(d["T2"][0]))),
    ("wrong ordinal", lambda d: d["T6"][0].update(member_ordinal=5)),
    ("reversed pair", lambda d: d["T7"][0].update(a_member_ordinal=1, b_member_ordinal=0,
                                                  a_candidate_validation_key=d["T7"][0]["b_candidate_validation_key"],
                                                  b_candidate_validation_key=d["T7"][0]["a_candidate_validation_key"])),
    ("element copy changed", lambda d: d["T6"][0].update(member_json=d["T6"][0]["member_json"].replace('"restated":false',
                                                                                                       '"restated":true'))),
    ("reordered members", lambda d: d["T6"].reverse() or [m.update(member_ordinal=i) for i, m in enumerate(d["T6"])]),
]


@pytest.mark.parametrize("name,tamper", TAMPER_VALIDATION, ids=[t[0] for t in TAMPER_VALIDATION])
def test_u10_python_mirror_refuses_every_validation_tamper_class(world, name, tamper):
    rows, reported = rows_of(world, "d1")
    assert codec.check_validation(rows, reported) == []
    bad = copy.deepcopy(rows)
    tamper(bad)
    assert codec.check_validation(bad, reported), name


def test_u10_python_mirror_refuses_nil_changes_and_cross_level_copy_corruption(world):
    rows, reported = rows_of(world, "d3")                                  # the nil SO
    bad = copy.deepcopy(rows)
    bad["T5"][0]["nil_forms"] = ["reported_nil:en_dash", "reported_nil_word:nil"]
    assert codec.check_validation(bad, reported)
    bad = copy.deepcopy(rows)
    bad["T6"][0].update(value_kind="numeric")
    assert codec.check_validation(bad, reported)
    rows, reported = rows_of(world, "d1")
    other = dict(reported)
    cid = rows["T6"][0]["candidate_id"]
    other[cid] = dict(other[cid], raw_value="9,999")                       # the F5 row no longer matches the copy
    assert codec.check_validation(rows, other)


TAMPER_RECORD = [
    ("state changed", lambda t13, t14, t15: t13.update(state="single_source")),
    ("interval changed", lambda t13, t14, t15: t13.update(interval_high=D(1))),
    ("role changed", lambda t13, t14, t15: t14[0].update(role_in_outcome="conflicting")),
    ("missing input", lambda t13, t14, t15: t14.pop()),
    ("reordered inputs", lambda t13, t14, t15: t14.reverse() or [i.update(observation_ordinal=n)
                                                                 for n, i in enumerate(t14)]),
    ("reversed cross pair", lambda t13, t14, t15: t15[0].update(
        a_observation_ordinal=t15[0]["b_observation_ordinal"], b_observation_ordinal=t15[0]["a_observation_ordinal"])),
    ("missing cross pair", lambda t13, t14, t15: t15.pop()),
    ("cross pair value", lambda t13, t14, t15: t15[0].update(a_value=D(0))),
    ("observation copy", lambda t13, t14, t15: t14[0].update(observation_json=t14[0]["observation_json"].replace(
        '"roles":["current"]', '"roles":["comparative"]'))),
]


@pytest.mark.parametrize("name,tamper", TAMPER_RECORD, ids=[t[0] for t in TAMPER_RECORD])
def test_u10_python_mirror_refuses_every_record_tamper_class(world, name, tamper):
    t13, t14, t15, f, sos = _record_env(world, lambda r: r.state == "corroborated" and len(r.comparisons) == 2)
    assert codec.check_record(t13, t14, t15, f, sos) == []
    t13b, t14b, t15b = copy.deepcopy(t13), copy.deepcopy(t14), copy.deepcopy(t15)
    tamper(t13b, t14b, t15b)
    assert codec.check_record(t13b, t14b, t15b, f, sos), name


def test_u10_python_mirror_refuses_batch_tampering(world):
    b = world["batch"]
    e6 = codec.e6(b)
    results = [{"result_ordinal": i, "ef_key": r.ef_key, "record_id": i, "appended": True}
               for i, r in enumerate(b.results)]
    hashes = {i: r.output_hash for i, r in enumerate(b.results)}
    row = {"output_json": e6, "configuration_id": b.configuration_id, "results_count": len(b.results),
           "records_appended": len(b.results)}
    assert codec.check_batch(row, results, hashes) == []
    assert codec.check_batch(row, results[:-1], hashes)
    assert codec.check_batch(dict(row, records_appended=len(b.results) - 1), results, hashes)
    swapped = copy.deepcopy(results)
    swapped[0]["result_ordinal"], swapped[1]["result_ordinal"] = 1, 0
    assert codec.check_batch(row, swapped, hashes)
    assert codec.check_batch(row, results, {**hashes, 0: "0" * 64})


def test_u10_key_texts_reproduce_f63_keys(world):
    for key, vr in world["vrs"].items():
        rows, _ = rows_of(world, key)
        assert codec.sha256_hex(codec.validation_run_key_text(rows["T1"])) == vr.key
        for r in rows["T2"]:
            assert codec.sha256_hex(codec.candidate_validation_key_text(vr.key, rows["T1"]["f5_run_id"], r)) == \
                r["candidate_validation_key"]
        for r in rows["T3"]:
            assert codec.sha256_hex(codec.op1_key_text(r)) == r["op1_key"]
        for s in rows["T5"]:
            assert codec.sha256_hex(codec.so_key_text(s)) == s["so_key"]
        for f in rows["T4"]:
            assert codec.sha256_hex(codec.ef_key_text(f)) == f["ef_key"]


def test_u10_identity_is_exactly_f6_identity_1(world):
    for sos in world["sos"].values():
        for so in sos:
            assert [f.name for f in dataclasses.fields(so.identity)] == list(identity.IDENTITY_FIELDS)
    row = rows_of(world, "d1")[0]["T4"][0]
    assert set(row) - {"ef_key", "first_validation_run_key"} == set(identity.IDENTITY_FIELDS)
    assert isinstance(row["period_end"], date)


# ------------------------------------------------------------------------------------------------ U10: every P18 case

# The database check P18 expects -> the Python mirror's report of the same rule. Structural refusals (keys, foreign
# keys, CHECKs, guards without a mirror class) only require that the mirror refuses; the seal (EDI-6) is database-only.
MIRROR_EQUIVALENT = {"copy of a member value": "EDI-3", "Asia/Colombo": "Asia/Colombo", "completeness": "completeness",
                     "issuer decision": "issuer decision",
                     "ef_key is not the f6.identity.1 hash": "ef_key is not the f6.identity.1 hash"}


@pytest.fixture(scope="module")
def tamper_world():
    """The tamper-only documents (two OP1 partitions, a three-member SO, twins), validated in memory."""
    docs = with_ids(tamper_docs(), start=1000)
    vrs = {k: d.validate() for k, d in docs.items()}
    return {"docs": docs, "vrs": vrs, "sos": {k: observations.build(vr) for k, vr in vrs.items()}}


def catalogue_rows(world, tamper_world, doc):
    """(rows, F5 reported values, issuer of the run's decision) of a catalogue document."""
    w = tamper_world if doc.startswith("t") else world
    rows, reported = rows_of(w, doc)
    link = w["vrs"][doc].issuer_link
    return rows, reported, link.issuer_id if link is not None else None


def mirror_markers(markers):
    return [m for m in markers if m.startswith("EDI-") and m != "EDI-6"] +         [MIRROR_EQUIVALENT[m] for m in markers if m in MIRROR_EQUIVALENT]


@pytest.mark.parametrize("name,doc,change,markers", [t[:4] for t in VALIDATION_TAMPERS],
                         ids=[t[0] for t in VALIDATION_TAMPERS])
def test_u10_the_writer_assertion_refuses_every_p18_validation_tamper(world, tamper_world, name, doc, change,
                                                                      markers):
    """The pre-insert assertion of jobs.validate (codec.check_validation) refuses exactly the cases the database
    refuses in P18 (tests/f64_tampers.py), before anything is sent, and names the same section 11.6 rule."""
    rows, reported, issuer = catalogue_rows(world, tamper_world, doc)
    assert codec.check_validation(rows, reported, issuer) == []
    bad = copy.deepcopy(rows)
    change(bad)
    assert exact(bad) != exact(rows), f"{name}: the change is a no-op"
    problems = codec.check_validation(bad, reported, issuer)
    assert problems, name
    want = MIRROR.get(name) or mirror_markers(markers)
    assert not want or any(w in p for p in problems for w in want), (name, want, problems)


def test_u10_the_writer_assertion_refuses_a_configuration_whose_versions_differ_from_e5(world):
    row = jobs.configuration_row(world["cfg"])
    assert codec.check_configuration(row) == []
    for col in ("reconciliation_version", "validation_version", "input_policy_version", "op1_version",
                "admission_version", "identity_version"):
        assert codec.check_configuration(dict(row, **{col: row[col][:-1] + "9"})), col


@pytest.fixture(scope="module")
def plan_d1_d2(world, tamper_world):
    """The in-memory partition plan of d1 + d2 + the twins (as P18 plans it from the database), with the stored-row
    views the writer's record check reads (facts and SOs with their members) and the twins map t13_other_twin uses."""
    keys = ("d1", "d2", "t3")
    ws = {"d1": world, "d2": world, "t3": tamper_world}
    sos = [o for k in keys for o in ws[k]["sos"][k]]
    cfg = configuration(*(ws[k]["docs"][k] for k in keys))
    batch = reconciliation.reconcile([ws[k]["vrs"][k].f5_run for k in keys], sos, cfg)
    so_by_key = {o.so_key: o for o in sos}
    items = [{"result": r, "prev": None, "append": True, "rows": codec.record_rows(r, so_by_key)}
             for r in batch.results]
    batch_row = {"batch_id": "b", "job_id": None, "configuration_id": cfg.configuration_id, "issuer_id": "i",
                 "partition_input_hash": "0" * 64, "output_hash": batch.output_hash, "output_json": codec.e6(batch),
                 "results_count": len(items), "records_appended": len(items)}
    facts, stored = {}, {}
    for k in keys:
        rows, _ = rows_of(ws[k], k)
        facts.update({f["ef_key"]: f for f in rows["T4"]})
        for s_row in rows["T5"]:
            stored[s_row["so_key"]] = {"row": s_row, "members": {m["candidate_validation_key"]: m for m in rows["T6"]
                                                                 if m["so_key"] == s_row["so_key"]}}
    plan = jobs.PartitionPlan(issuer_id="i", configuration_id=cfg.configuration_id, batch=batch, items=items,
                              batch_row=batch_row, chosen=[])
    a, b = sorted((m for m in rows_of(tamper_world, "t3")[0]["T6"]), key=lambda m: m["member_ordinal"])
    plan.twins = {a["candidate_validation_key"]: b["candidate_validation_key"],
                  b["candidate_validation_key"]: a["candidate_validation_key"]}
    return plan, facts, stored


def _write_in_memory(monkeypatch, plan, facts, stored):
    """jobs.write_partition(check=True) with its database calls answered in memory: the real assertion path."""
    ids = iter(range(1, 1000))
    monkeypatch.setattr(writer, "insert_batch", lambda cur, row: "batch")
    monkeypatch.setattr(writer, "insert_record", lambda cur, t13, t14, t15, batch_id, last: next(ids))
    monkeypatch.setattr(writer, "insert_results", lambda cur, rows: None)
    monkeypatch.setattr(jobs, "_fact", lambda cur, ef_key: facts[ef_key])
    monkeypatch.setattr(jobs, "_records_for", lambda cur, keys: {k: stored[k] for k in keys if k in stored})
    return jobs.write_partition(None, plan, check=True)


def _mirror_problems(plan, facts, stored):
    """Every problem the writer's checks report for a plan, in jobs.write_partition's order (untruncated)."""
    out, results, hashes = [], [], {}
    for i, it in enumerate(plan.items):
        r = it["result"]
        if it["append"]:
            t13, t14, t15 = it["rows"]
            out += codec.check_record(dict(t13), [dict(x) for x in t14], [dict(x) for x in t15], facts[r.ef_key],
                                      {k: stored[k] for k in (o.so_key for o in r.observations) if k in stored},
                                      plan.batch.configuration.reconciliation_version)
        hashes[i] = r.output_hash
        results.append({"result_ordinal": i, "ef_key": r.ef_key, "record_id": i, "appended": it["append"]})
    return out + codec.check_batch(plan.batch_row, results, hashes)


@pytest.mark.parametrize("name,change,markers", [t[:3] for t in RECORD_TAMPERS], ids=[t[0] for t in RECORD_TAMPERS])
def test_u10_the_writer_assertion_refuses_every_p18_record_tamper(monkeypatch, plan_d1_d2, name, change, markers):
    plan, facts, stored = plan_d1_d2
    assert sorted(i["result"].state for i in plan.items) == ["conflicting", "corroborated", "single_source",
                                                             "single_source"]
    assert _write_in_memory(monkeypatch, copy.deepcopy(plan), facts, stored) == "batch"      # the genuine plan
    assert _mirror_problems(plan, facts, stored) == []
    bad = copy.deepcopy(plan)
    change(bad)
    assert exact([bad.items, bad.batch_row]) != exact([plan.items, plan.batch_row]), f"{name}: the change is a no-op"
    with pytest.raises(codec.CodecError):
        _write_in_memory(monkeypatch, copy.deepcopy(bad), facts, stored)
    problems = _mirror_problems(bad, facts, stored)
    want = MIRROR.get(name) or mirror_markers(markers)
    assert not want or any(w in p for p in problems for w in want), (name, want, problems)


def test_u10_the_writer_assertion_refuses_a_candidate_of_another_f5_run(world):
    """A consistent forgery pointing a candidate validation (and its member) at the candidate with the SAME
    coordinates in another F5 run of the same document: only the run-membership check can see it."""
    rows, reported = rows_of(world, "d1")
    other = scenario_docs()["d1"]
    other.run_id, other.versions = "run-9101-101-second", dict(other.versions, mapper_version="f5.map.second")
    with_ids({"x": other}, start=5000)
    bad = copy.deepcopy(rows)
    c = next(x for x in bad["T2"] if x["admitted"])
    twin = next(x for x in other.candidates if (x["statement_index"], x["row_index"], x["column_index"],
                                                x["value_ordinal"]) == (c["statement_index"], c["row_index"],
                                                                        c["column_index"], c["value_ordinal"]))
    candidate_of_other_run(bad, twin["id"])
    problems = codec.check_validation(bad, reported)
    assert "T2 candidate_id is not a candidate of this F5 run" in problems, problems


def test_the_writer_resolves_a_concurrent_duplicate_on_the_natural_key(world, monkeypatch):
    """ON CONFLICT arbitrates the content key only: when a concurrent job wins the race for the SAME validation run,
    the natural key (uq_fvr_input_set) can fail first. The committed row decides: the same content key with the same
    hashes is `already_present`; anything else is nondeterminism."""
    import psycopg2.errors
    rows, _ = rows_of(world, "d1")
    t1 = rows["T1"]

    def natural_key_violation(cur, table, cols, rows_, suffix=""):
        raise psycopg2.errors.UniqueViolation("duplicate key value violates unique constraint \"uq_fvr_input_set\"")
    monkeypatch.setattr(writer, "_insert", natural_key_violation)

    class Cursor:
        def __init__(self, stored):
            self.stored, self.sql = stored, []

        def execute(self, sql, args=None):
            self.sql.append(sql)

        def fetchone(self):
            return self.stored
    cur = Cursor((t1["input_hash"], t1["output_hash"]))
    assert writer.insert_validation(cur, rows) == "already_present"
    assert "rollback to savepoint f6_t1" in cur.sql
    for stored in (None, (t1["input_hash"], "0" * 64)):
        with pytest.raises(writer.NondeterminismError):
            writer.insert_validation(Cursor(stored), rows)

