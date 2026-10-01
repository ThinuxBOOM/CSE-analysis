"""
Real-data validation (docs/REAL_DATA_VALIDATION_DESIGN.md): the evidence bundle, its pinned manifest (section 5.3),
and its REPLAY into a throwaway PostgreSQL database through the frozen production stores only (section 5).

This is validation tooling, not a production ingestion path (RDV-B3):
- nothing in worker/ imports it;
- it writes only to a database whose name starts with `rdv_` (created by build_database), and only as cse_worker;
- it calls no CSE client function and opens no network connection (RDV-B4; the Linux runs use
  `docker run --network none`);
- it invents nothing (RDV-R3): every F1 / P2 / F5 / F3 row it causes is written by frozen code from real captured
  evidence.

Evidence (design section 4):
  E-A  $CSE_F6_CORPUS_DIR   the 26-filing F6 corpus: F1 listing fields, F3 classification, F5 result, F2 retrieval
                            record (F6.0, 2026-09-27; outside Git)
  E-B  $CSE_F0_CAPTURE_DIR  the 2026-09-24 F0 captures: the feed by year, /api/financials (COMB; 9 symbols) and
                            allSecurityCode (outside Git)
  E-C  tests/fixtures/      eight real companyInfoSummery bodies (in Git since Stage E)
"""
import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(__file__))

from worker import issuer_identity as ii  # noqa: E402
from worker import report_discovery as rd  # noqa: E402
from worker.cse_client import CSEResponse  # noqa: E402  (a dataclass only: no request function is ever called)

RDV_VERSION = "rdv.1"
CORPUS_ENV = "CSE_F6_CORPUS_DIR"
CAPTURE_ENV = "CSE_F0_CAPTURE_DIR"
COLOMBO = timezone(timedelta(hours=5, minutes=30), "Asia/Colombo")
DB_PREFIX = "rdv_"

# --------------------------------------------------------------------------------------------- the pinned manifest
# E-A: file -> (SHA-256, bytes)
CORPUS = {
    "benchmark_32216.json": ("11f8eee7e11ed942490ccf13bebdd09c2a7be002c3d0f431c35a250a60284b88", 102070),
    "benchmark_45857.json": ("0b75a490f29b1f63908bd685138dc74fab21f513caa9b74b6d94e0286faf5d60", 134978),
    "benchmark_47478.json": ("e04fca2a6cfd37aa1c2aa6cd2f887be17459ebd1ea4669a64080f2340d3c32d9", 170438),
    "benchmark_48292.json": ("cde0585ea8b18d1a5e9be9b1b732739b85b7fe84eef2177f985fc833803044b6", 88097),
    "benchmark_48576.json": ("0e327fae517a9088bd68dc3e4b3688790910ccb45732bd878633b284398bcce0", 14029),
    "benchmark_49086.json": ("8b4f829b878036b8034b7d85931ff3bd91771529ccdd8fca28251a54d846f8a2", 86949),
    "benchmark_49117.json": ("df3b54508d96e61c400e0c89308f4fdaa5d6781b10f1a8f43d7af035ccb2b313", 4567),
    "benchmark_49384.json": ("8d6e7040d2469e3b104866b933da5d969059cc14eb8b897b29bc4589f819d94b", 151634),
    "benchmark_50553.json": ("b952416ea4c111e44248515f5317425483aa716660d59d0e1d6bfd79ecfef17e", 53527),
    "benchmark_51712.json": ("12319e20345d3156bd31c2c1359b75818f576cb241af54949b84b032d7f36ad5", 142355),
    "benchmark_52157.json": ("ffc21fe56676d71be530878c17116c535e91e73c8883695369a4477d0b3add1c", 137169),
    "benchmark_52620.json": ("72e3b30c9b163a13d3baf0afad010e86b787e9bebac246a828ab8e41944d3b84", 109931),
    "benchmark_52684.json": ("f5569e2b69cbbab0f55749fc4f305cf93220c60c1100127e62c532c9dcae52fb", 121671),
    "benchmark_52713.json": ("5c5b9144ce9d26f918735cee0c0a2a65d5080510575a970827a8772c2a6eddb3", 147747),
    "benchmark_52749.json": ("28d21897260551038cb22b7a952a8fc63de941cca5d306937aabffa1cac08c69", 115694),
    "benchmark_52860.json": ("75abeccdcc1fb30d5a0bd89cde7ee115f90c40885c9e3a23d0c9390a39b8dc62", 49629),
    "benchmark_53067.json": ("07679d897fc6fa85a27cc4e9e8dad3f0f35e4f0d5c227ac195f9cdcdc9150ab4", 101641),
    "benchmark_53096.json": ("484715376c7fe4dbf9d5be8a40311503db1d2674b4466a36390bf466932034b6", 108909),
    "benchmark_53129.json": ("163f167767bc74ea6e8628c7e495283a7bffe800786c57a0353827e568c84932", 57250),
    "pairs_47026.json": ("5c62c7409117ca153a9254e111fc870c6c453a64c0ba2764cf34b37c12887455", 150775),
    "pairs_50613.json": ("cb77d1e6c3acc03d49880c4b91906dbe08a61ae64f73495e632814ec783b327f", 153469),
    "pairs_50738.json": ("fb838ed9d9b8a1435d77b033b83fbe54f8783185721490d4bdae29526caa7b8d", 303691),
    "pairs_50922.json": ("5fc0cbbb9b04da4a13964160435bef0aaf530517db661e3d3b0cc4812d3548bf", 48077),
    "pairs_51372.json": ("a0705abd833c50e529e413be22a879084fef0c0d8da3cb8ae3a43e88e8fd9baa", 49252),
    "pairs_52319.json": ("0463c4698830db749b9e559cf0709eb18369749fe34d9d5370b9c1d379800773", 62947),
    "pairs_52888.json": ("7f4873b439e7be24f9ed6f54b7129dcffcf0aa0f66dc22266a42e4835f9ce000", 100853),
}
# E-B: file -> (evidence id, SHA-256, bytes, capture time). The capture time is the F0 capture file's modification time
# (UTC), the closest recorded system-knowledge time of the 2026-09-24 capture session (design section 5.4).
CAPTURES = {
    "fin_feed_by_year.json": ("E-B1", "1cd0f3f098a76a91fe4c936346460f46ea4d2f352872d750fa5a2f42b8d2eb29", 4100112,
                              "2026-09-24T13:41:59.969454+00:00"),
    "financials_COMB.json": ("E-B2", "ef303de550332984e5d14511f847183f48372c29cd8a8c6289881860c4a8e83c", 43462,
                             "2026-09-24T13:40:18.995555+00:00"),
    "financials_sample.json": ("E-B3", "825beca934f15f65a6a17f873e1ee1f87a07967b541ce20a8c038956261c695e", 243244,
                               "2026-09-24T13:44:29.106176+00:00"),
    "allSecurityCode.json": ("E-B4", "aca2bbc8ec9c92c4f96595c830eb55b6fb7dae2a7178d1cb6a18fb0f6447c2a8", 27791,
                             "2026-09-24T13:43:08.827609+00:00"),
}
# E-C: repository path -> (query symbol, SHA-256 of the LF-normalised file, capture date, basis of that date)
_SESSION = "the 2026-09-04 session named in tests/p2_fakes.py"
COMPANY_INFO = {
    "tests/fixtures/multi_company/real_companyInfoSummery_COMB_N0000.json":
        ("COMB.N0000", "5912a05963d046ac9466a81630f4e78d89bee77e96ea068b75f60f96bf11e896", "2026-09-04", _SESSION),
    "tests/fixtures/multi_company/real_companyInfoSummery_HNB_N0000.json":
        ("HNB.N0000", "3729b740c75796d0d6b26e28c94f46efcd2bde08473dc0ef460533687e2d4156", "2026-09-04", _SESSION),
    "tests/fixtures/multi_company/real_companyInfoSummery_JKH_N0000.json":
        ("JKH.N0000", "826c6de3be4635322918892b9d433db9cbc7688eff36044fc4f934a844587d89", "2026-09-04", _SESSION),
    "tests/fixtures/multi_company/real_companyInfoSummery_LOLC_N0000.json":
        ("LOLC.N0000", "87d4557870819fdb31233a47a5b6d5a06e330eb2bac023bd0f5526779dcb86cb", "2026-09-04", _SESSION),
    "tests/fixtures/multi_company/real_companyInfoSummery_SAMP_N0000.json":
        ("SAMP.N0000", "167196e814143506e6f40c876a3bb19bbed6a633baba19ce26d80d4a0c5a06ac", "2026-09-04", _SESSION),
    "tests/fixtures/real_companyInfoSummery_COMB_20260901.json":
        ("COMB.N0000", "49894a87f07b4b985f01e3f1fd70d7ec630879e4b86b220cc6a02e56d5e92195", "2026-09-01", "file name"),
    "tests/fixtures/windows/real_midsession_companyInfoSummery_CARS_N0000_20260924.json":
        ("CARS.N0000", "827deafc8f9818d63349ed8cea45f9766dc155e8e7f4eabf78b2fbeba4a04d76", "2026-09-24", "file name"),
    "tests/fixtures/windows/real_postclose_companyInfoSummery_SOY_N0000_20260923.json":
        ("SOY.N0000", "41e6bf8df3b01599cf231acac0e89d9002bf41e9b5f28b9311b19acb83fcde82", "2026-09-23", "file name"),
}


def sha256_of(path, lf=False):
    with open(path, "rb") as f:
        data = f.read()
    if lf:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest(), len(data)


def manifest():
    """The pinned manifest as one canonical object; its digest is recorded in every report."""
    return {"rdv_version": RDV_VERSION,
            "corpus": {k: list(v) for k, v in sorted(CORPUS.items())},
            "captures": {k: list(v) for k, v in sorted(CAPTURES.items())},
            "company_info": {k: list(v) for k, v in sorted(COMPANY_INFO.items())}}


def manifest_digest():
    return hashlib.sha256(json.dumps(manifest(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Bundle:
    corpus_dir: str
    capture_dir: str
    repo: str = REPO


def locate():
    """The bundle named by the environment, or None (the tests then skip; the evidence is not in Git)."""
    corpus, captures = os.environ.get(CORPUS_ENV), os.environ.get(CAPTURE_ENV)
    return Bundle(corpus, captures) if corpus and captures else None


def manifest_problems(bundle):
    """Every way the bundle differs from the pinned manifest (RDV-R4). Empty when it is exactly the evidence."""
    problems = []
    present = {n for n in os.listdir(bundle.corpus_dir) if n.endswith(".json") and not n.endswith("__summary.json")}
    for name in sorted(present - set(CORPUS)):
        problems.append(f"E-A: unexpected corpus file {name}")
    for name, (sha, size) in sorted(CORPUS.items()):
        path = os.path.join(bundle.corpus_dir, name)
        if not os.path.isfile(path):
            problems.append(f"E-A: missing {name}")
        elif sha256_of(path) != (sha, size):
            problems.append(f"E-A: {name} differs from the manifest")
    for name, (_, sha, size, _) in sorted(CAPTURES.items()):
        path = os.path.join(bundle.capture_dir, name)
        if not os.path.isfile(path):
            problems.append(f"E-B: missing {name}")
        elif sha256_of(path) != (sha, size):
            problems.append(f"E-B: {name} differs from the manifest")
    for rel, (_, sha, _, _) in sorted(COMPANY_INFO.items()):
        path = os.path.join(bundle.repo, *rel.split("/"))
        if not os.path.isfile(path):
            problems.append(f"E-C: missing {rel}")
        elif sha256_of(path, lf=True)[0] != sha:
            problems.append(f"E-C: {rel} differs from the manifest")
    return problems


def require(bundle):
    problems = manifest_problems(bundle)
    if problems:
        raise RuntimeError("the evidence differs from the pinned manifest (RDV-R4): " + "; ".join(problems))


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def corpus(bundle):
    """{cse_filing_id: corpus document}, every pinned file, ascending filing id."""
    docs = {}
    for name in sorted(CORPUS):
        d = _load(os.path.join(bundle.corpus_dir, name))
        docs[d["result"]["run"]["cse_filing_id"]] = d
    return dict(sorted(docs.items()))


def capture(bundle, name):
    return _load(os.path.join(bundle.capture_dir, name))


def captured_at(name):
    return datetime.fromisoformat(CAPTURES[name][3])


def company_info_observed_at(rel):
    """Date-level precision only: 00:00 Asia/Colombo of the documented capture date (design section 5.4)."""
    return datetime.fromisoformat(COMPANY_INFO[rel][2]).replace(tzinfo=COLOMBO)


# --------------------------------------------------------------------------------------------- F1: listing replays

@dataclass(frozen=True)
class ReplayRun:
    """One captured response, replayed as one F1 discovery run (RDV-R2)."""
    evidence: str
    source_endpoint: str
    key: str                     # the feed year, or the /api/financials query symbol
    request_params: dict         # the replay record: no CSE request parameter is claimed
    now: datetime                # the capture time
    observations: tuple          # FilingObservation, in captured order
    rejected: tuple              # (reason, raw item) for items F1 cannot record
    unrecognised_list_keys: tuple


def _replay_record(evidence, name, key):
    _, sha, _, at = CAPTURES[name]
    return {"replay": {"evidence": evidence, "file": name, "key": key, "sha256": sha, "captured_at": at,
                       "rdv_version": RDV_VERSION}}


def _parse(items, endpoint, bucket, symbol):
    observations, rejected = [], []
    for item in items:
        try:
            observations.append(rd.parse_listing_item(item, endpoint, bucket, symbol))
        except rd.ItemRejected as exc:
            rejected.append((str(exc), item))
    return observations, rejected


def f1_runs(bundle):
    """Every captured listing response as a ReplayRun: the feed year by year (E-B1), then /api/financials for
    COMB (E-B2) and for each sampled symbol in symbol order (E-B3). F1's own parsing functions only."""
    runs = []
    feed = capture(bundle, "fin_feed_by_year.json")
    for year in sorted(feed):
        obs, rej = _parse(feed[year], rd.FEED_ENDPOINT, rd.FEED_BUCKET, None)
        runs.append(ReplayRun("E-B1", rd.FEED_ENDPOINT, year, _replay_record("E-B1", "fin_feed_by_year.json", year),
                              captured_at("fin_feed_by_year.json"), tuple(obs), tuple(rej), ()))
    listings = [("E-B2", "financials_COMB.json", "COMB.N0000", capture(bundle, "financials_COMB.json"))]
    sample = capture(bundle, "financials_sample.json")
    listings += [("E-B3", "financials_sample.json", sym, sample[sym]) for sym in sorted(sample)]
    for evidence, name, symbol, body in listings:
        response = CSEResponse(endpoint=rd.LISTING_ENDPOINT, request_method="POST", request_params={},
                               status_code=None, ok=True, body=body)
        buckets, unrecognised, category, reason = rd.extract_listing_buckets(response)
        if category or buckets is None:
            raise RuntimeError(f"{name} {symbol}: not a listing body ({category}: {reason})")
        obs, rej = [], []
        for bucket, items in buckets.items():
            o, r = _parse(items, rd.LISTING_ENDPOINT, bucket, symbol)
            obs.extend(o)
            rej.extend(r)
        runs.append(ReplayRun(evidence, rd.LISTING_ENDPOINT, symbol, _replay_record(evidence, name, symbol),
                              captured_at(name), tuple(obs), tuple(rej), tuple(unrecognised)))
    return runs


def replay_f1(store, runs):
    """Each run through the F1 store API exactly as F1's discovery functions drive it (PostgresFilingStore, or
    InMemoryFilingStore for the database-free prediction): begin_run; apply_observation per item, where a failing
    item is recorded and the rest go on; finish_run; commit."""
    report = []
    for run in runs:
        run_id = store.begin_run(run.source_endpoint, run.request_params, run.now)
        outcomes, failures, warned = Counter(), [], 0
        for obs in run.observations:
            try:
                outcome, _ = store.apply_observation(obs, run_id, run.now)
            except Exception as exc:  # noqa: BLE001 - as F1's _ingest: one item's failure must not lose the rest
                failures.append({"cse_filing_id": obs.cse_filing_id, "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            outcomes[outcome] += 1
            warned += bool(obs.warnings)
        status = "partial" if run.rejected or failures else "succeeded"
        store.finish_run(run_id, {
            "status": status, "failure_category": None, "http_status": None,
            "rows_returned": len(run.observations) + len(run.rejected),
            "filings_new": outcomes["new_filing"],
            "observations_new": sum(n for k, n in outcomes.items() if k != "unchanged"),
            "metadata_changes": outcomes["metadata_changed"], "rows_rejected": len(run.rejected),
            "item_failures": len(failures),
            "details": {"replay": run.request_params["replay"], "rejected": [r for r, _ in run.rejected][:50],
                        "item_failures": failures[:50], "unrecognised_list_keys": list(run.unrecognised_list_keys),
                        "items_with_warnings": warned}}, run.now)
        store.commit()
        report.append({"evidence": run.evidence, "endpoint": run.source_endpoint, "key": run.key,
                       "items": len(run.observations) + len(run.rejected), "rejected": len(run.rejected),
                       "failures": len(failures), "outcomes": dict(sorted(outcomes.items())), "warned": warned})
    return report


# --------------------------------------------------------------------------------------------- F5: issuer evidence

def identifier_observations(bundle):
    """Every issuer-identifier observation the evidence carries, through F5's own extraction functions, in a fixed
    order: E-C companyInfoSummery bodies, then /api/financials secIds (E-B2, E-B3), then allSecurityCode (E-B4)."""
    out = []
    for rel in sorted(COMPANY_INFO):
        symbol = COMPANY_INFO[rel][0]
        body = _load(os.path.join(bundle.repo, *rel.split("/")))
        out += ii.observations_from_company_info(body, symbol, company_info_observed_at(rel).isoformat(),
                                                 f"rdv:E-C:{rel}")
    at = captured_at("financials_COMB.json").isoformat()
    out += ii.observations_from_financials(capture(bundle, "financials_COMB.json"), "COMB.N0000", at,
                                           "rdv:E-B2:financials_COMB.json")
    sample, at = capture(bundle, "financials_sample.json"), captured_at("financials_sample.json").isoformat()
    for sym in sorted(sample):
        out += ii.observations_from_financials(sample[sym], sym, at, f"rdv:E-B3:financials_sample.json#{sym}")
    out += ii.observations_from_all_security_codes(capture(bundle, "allSecurityCode.json"),
                                                   captured_at("allSecurityCode.json").isoformat(),
                                                   "rdv:E-B4:allSecurityCode.json")
    return out


def security_master(bundle):
    """P2's own reading of the allSecurityCode capture: (universe entries, duplicate symbols)."""
    from worker.market_capture import derive
    return derive.universe_entries(capture(bundle, "allSecurityCode.json"))


def predicted_decisions(bundle):
    """Design section 6.4: the frozen F5 decision functions over the same evidence, with no database.
    - F1 runs through InMemoryFilingStore (same merge functions as the Postgres store).
    - Observations are de-duplicated on 0007's unique key, as the database does.
    - Security decisions are made only for security-master rows, as resolve_securities does.
    - The reuse guard is applied over every observed symbol.
    Returns {cse_filing_id: FilingDecision} for the corpus filings."""
    universe, _ = security_master(bundle)
    companies = {e["symbol"] for e in universe if e["name"]}           # ensure_companies never invents a nameless row
    f1 = rd.InMemoryFilingStore(companies_by_ticker={s: f"company:{s}" for s in companies})
    replay_f1(f1, f1_runs(bundle))
    seen, by_symbol = set(), {}
    for o in identifier_observations(bundle):
        key = (o["source_endpoint"], o["source_field"], o["query_symbol"], o["symbol"], o["payload_sha256"])
        if key in seen:
            continue
        seen.add(key)
        if o["symbol"] is not None:                                     # issuer_store reads `symbol is not null`
            by_symbol.setdefault(o["symbol"], []).append(dict(o, id=len(seen)))
    decisions = {s: d for s, d in ii.decide_securities(by_symbol).items() if s in companies}
    issuers = {d.sec_id: f"issuer:{d.sec_id}" for d in decisions.values() if d.link_status == "evidenced"}
    links = {s: (d.link_status, d.sec_id) for s, d in decisions.items()}
    disputed = set(ii.disputed_sec_ids(by_symbol))
    out = {}
    for fid in corpus(bundle):
        row = f1.filings[fid]
        out[fid] = ii.decide_filing(fid, row["path"], row["listing_symbols"], links, issuers, disputed)
    return out


# --------------------------------------------------------------------------------------------- the database replay

def _guard(conn):
    with conn.cursor() as cur:
        cur.execute("select session_user, current_database()")
        user, db = cur.fetchone()
    conn.rollback()
    if user != "cse_worker" or not db.startswith(DB_PREFIX):
        raise RuntimeError(f"the replay writes only as cse_worker into a throwaway {DB_PREFIX}* database "
                           f"(connected as {user} to {db})")


def persist_filing(conn, issuer_store, doc):
    """One corpus filing in ONE transaction, exactly in extract_financial_candidates._persist's order: F3
    classification, its id, the filing's issuer decision (frozen f5.issuer.2), the F5 run."""
    from worker.financial_candidates_store import PostgresCandidateStore, classification_id
    from worker.report_classification_store import PostgresClassificationStore
    c, result, retrieval = doc["classification"], doc["result"], doc["retrieval"]
    fid = result["run"]["cse_filing_id"]
    try:
        f3 = PostgresClassificationStore(conn).save(c, retrieval.get("byte_length"))
        cid = classification_id(conn, c)
        link = issuer_store.link_filing(fid)
        f5, run_id = PostgresCandidateStore(conn).save(result, cid, link)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"cse_filing_id": fid, "f3": f3, "classification_id": cid, "f5": f5, "f5_run_id": run_id,
            "issuer_link": {k: link[k] for k in ("id", "status", "basis")} if link else None}


def replay(conn, bundle):
    """Design section 5.2, as cse_worker: security master (P2), F1 discovery runs, issuer evidence and decisions
    (F5), then F3 + issuer decision + F5 per corpus filing. Returns the replay report."""
    _guard(conn)
    require(bundle)
    from worker.issuer_store import PostgresIssuerStore
    from worker.market_capture import derive
    from worker.report_filings_store import PostgresFilingStore
    report: dict = {"rdv_version": RDV_VERSION, "manifest_sha256": manifest_digest()}
    universe, duplicates = security_master(bundle)
    _, created = derive.ensure_companies(conn, universe, {}, captured_at("allSecurityCode.json"))
    report["security_master"] = {"entries": len(universe), "duplicate_symbols": duplicates,
                                 "created": len(created["created"]),
                                 "not_created_no_name": created["not_created_no_name"]}
    report["f1"] = replay_f1(PostgresFilingStore(conn), f1_runs(bundle))
    issuer = PostgresIssuerStore(conn)
    new = issuer.record_observations(identifier_observations(bundle))
    report["issuer"] = {"observations_new": new, "securities": issuer.resolve_securities()}
    issuer.commit()
    report["filings"] = [persist_filing(conn, issuer, doc) for doc in corpus(bundle).values()]
    return report


# --------------------------------------------------------------------------------------------- the whole pipeline

def new_database(cluster):
    """A fresh, fully migrated throwaway database (a copy of f64_support's migrated template)."""
    import f64_support as S
    name = f"{DB_PREFIX}{uuid.uuid4().hex[:12]}"
    su = cluster.connect(dbname="postgres", user="postgres")
    su.autocommit = True
    with su.cursor() as cur:
        cur.execute(f"create database {name} template {S.TEMPLATE} owner cse_owner")
        cur.execute(f"revoke all on database {name} from public")
        cur.execute(f"grant connect on database {name} to cse_migrator, cse_worker, cse_reader, cse_backup")
    su.close()
    return name


def reader_conn(cluster, db):
    """A session with exactly cse_reader's privileges. cse_reader is NOLOGIN (a group for later analysis logins), so
    the throwaway cluster's bootstrap superuser session switches to it with SET ROLE, as the F6.4 role tests do. No
    role is created or granted anything."""
    c = cluster.connect(dbname=db, user="postgres")
    with c.cursor() as cur:
        cur.execute("set role cse_reader")
        cur.execute("select current_user, current_setting('is_superuser')")
        got = cur.fetchone()
    c.commit()
    if got != ("cse_reader", "off"):
        c.close()
        raise RuntimeError(f"could not act as cse_reader: {got}")
    return c


DESIGNATION_NOTE = "real-data validation (rdv.1): every F3/F4/F5 version present in the replayed evidence"


def build_database(cluster, bundle):
    """Replay (section 5) and the F6.4 execution of section 7, through F6.4's own jobs:
    - the worker preflight;
    - validate every pending F5 run;
    - register the all-present configuration;
    - the owner designation (owner path);
    - reconcile the designated configuration.
    Returns {"database", "replay", "validate", "configuration", "designation", "reconcile"}."""
    import f64_support as S
    from worker.financial_truth_store import jobs, preflight
    db = new_database(cluster)
    w = S.conn(cluster, db, "cse_worker")
    try:
        out = {"database": db, "replay": replay(w, bundle)}
        problems = preflight.problems(w)
        if problems:
            raise RuntimeError(f"F6.4 preflight refused: {problems}")
        with w.cursor() as cur:
            pending = jobs.pending_runs(cur)
        w.rollback()
        rev = jobs.code_revision()
        out["validate"] = [jobs.validate(w, r, code_revision=rev) for r in pending]
        with w.cursor() as cur:
            cfg = jobs.configuration_from_present_runs(cur)
        w.rollback()
        out["configuration"] = jobs.register_configuration(w, cfg)
        m = S.conn(cluster, db, "cse_migrator")
        try:
            out["designation"] = jobs.designate(m, cfg.configuration_id, DESIGNATION_NOTE, os_user="rdv")
        finally:
            m.close()
        with w.cursor() as cur:
            designated = jobs.designated_configuration(cur)
        w.rollback()
        out["reconcile"] = jobs.reconcile(w, designated, code_revision=rev)
        return out
    finally:
        w.close()


# --------------------------------------------------------------------------------------------- determinism (writing)

def repeat_jobs(cluster, db):
    """D1 + D2:
    - every F5 run is validated again, which must give `already_present` (the writer compares the stored hashes);
    - the designated configuration is reconciled again, which must leave every partition `unchanged`.
    No row may be added to any F6 data table; only the job ledger records the attempts."""
    import f64_support as S
    import rdv_measure as M
    from worker.financial_truth_store import jobs
    w = S.conn(cluster, db, "cse_worker")
    try:
        with w.cursor() as cur:
            before = M.table_counts(cur)
            cur.execute("select id from financial_extraction_runs order by id")
            runs = [str(r[0]) for r in cur.fetchall()]
            cid = M.designated(cur)
        w.rollback()
        states = Counter(jobs.validate(w, r)["state"] for r in runs)
        rec = jobs.reconcile(w, cid)
        with w.cursor() as cur:
            after = M.table_counts(cur)
        w.rollback()
        return {"validate": dict(sorted(states.items())),
                "reconcile": {k: rec.get(k) for k in ("state", "partitions", "written", "unchanged", "failed")},
                "rows_unchanged": before == after, "before": before, "after": after}
    finally:
        w.close()


def nondeterminism_refused(cluster, db, cse_filing_id):
    """D6: a DIFFERENT result for an existing natural key is refused, and nothing is overwritten.
    - The stored run's document context is altered in memory only (its F3 document type).
    - That changes the content key but not the natural key (F5 run, versions, issuer decision, publication instant).
    - The rows are well formed: the codec's section 11.6 check passes them. Only the writer's natural-key rule
      refuses them."""
    import dataclasses
    import decimal
    import f64_support as S
    import rdv_measure as M
    from worker.financial_truth import admission, observations
    from worker.financial_truth_store import F6_DECIMAL_CONTEXT, STORE_VERSION, codec, jobs, loader, writer
    w = S.conn(cluster, db, "cse_worker")
    try:
        with w.cursor() as cur:
            loader.session(cur)
            cur.execute("select validation_run_key, f5_run_id, issuer_link_id, publication_uploaded_at, input_hash, "
                        "output_hash from financial_validation_runs where cse_filing_id = %s", (cse_filing_id,))
            key, run_id, link_id, uploaded_at, input_hash, output_hash = cur.fetchone()
            before = M.table_counts(cur)
            result, run = loader.f5_result(cur, run_id)
            ref = loader.f5_run_ref(cur, run_id, result, run)
            link = loader.issuer_link(cur, link_id)
            doc = loader.document(cur, run["classification_id"])
            other = "annual_report" if doc.document_type != "annual_report" else "interim_financial_statements"
            with decimal.localcontext(F6_DECIMAL_CONTEXT):
                vr = admission.validate_run(result, f5_run=ref, issuer_link=link, uploaded_at=uploaded_at,
                                            document=dataclasses.replace(doc, document_type=other))
                sos = observations.build(vr)
            now = datetime.now(timezone.utc)
            rows = codec.validation_rows(vr, sos, store_version=STORE_VERSION, code_revision=None, job_id=None,
                                         started_at=now, finished_at=now)
            problems = codec.check_validation(rows, jobs.f5_reported(result), link.issuer_id if link else None)
            refused = None
            try:
                writer.insert_validation(cur, rows)
            except writer.NondeterminismError as exc:
                refused = str(exc)
        w.rollback()
        with w.cursor() as cur:
            after = M.table_counts(cur)
            cur.execute("select input_hash, output_hash from financial_validation_runs where validation_run_key = %s",
                        (key,))
            stored = cur.fetchone()
        w.rollback()
        return {"cse_filing_id": cse_filing_id, "content_key_differs": vr.key != key, "codec_problems": problems,
                "refused": refused, "stored_unchanged": stored == (input_hash, output_hash) and before == after}
    finally:
        w.close()
