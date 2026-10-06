"""
F5 runs as evidence (design sections 9 HB-R3/HB-R4 and 15.3: "an F5 run exists -> persisted"). Read-only.

A document item is (filing, path version): `document:<cse_filing_id>:<SHA-256 of the path>`. An F5 run row records
the filing and the document's SHA-256, never the path, so a run is this item's evidence only when it is provably the
document of THIS path version, under the armed versions (the stage and tool versions of design section 10.2):

  1. ledger-linked: its document SHA-256 is that of a succeeded F2 retrieval (L6) of this item, deletion verified; or
  2. single-path filing: F1 has only ever seen one path for the filing (every listing version of it, in
     report_filing_observations, names the same path, the item's), so any F5 run of the filing came from that path.
     F5's own run() reads the current path, and F1 records every path it has seen; a run made outside Phase 2 (a manual
     F5 CLI run) is attributed only on this proof.

Anything else (a filing whose path changed, a run of another path version, a run under other versions) is not this
item's evidence: the item is retrieved, and F5's own idempotency makes an identical document 'already_present'.
"""
from .. import financial_candidates as f5, financial_concepts as fc, report_classification as rc
from .. import statement_extraction as se
from ..financial_backfill import keys
from . import tools

VERSION_COLUMNS = ("classifier_version", "text_extractor", "word_extractor", "f4_extractor_version",
                   "builder_version", "mapper_version", "vocabulary_version")


def armed_versions():
    """The F3/F4/F5 versions an F5 run must carry to count under the armed version tuple (section 10.2)."""
    return {"classifier_version": rc.CLASSIFIER_VERSION, "text_extractor": tools.pinned_text_extractor(),
            "word_extractor": tools.pinned_word_extractor(), "f4_extractor_version": se.F4_EXTRACTOR_VERSION,
            "builder_version": f5.F5_BUILDER_VERSION, "mapper_version": fc.MAPPER_VERSION,
            "vocabulary_version": fc.VOCABULARY_VERSION}


def _q(conn, sql, args=(), fetch="all"):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() if fetch == "all" else cur.fetchone()
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    return rows


_VERSIONED = " and ".join(f"r.{c} = %({c})s" for c in VERSION_COLUMNS)


def linked_run(conn, item_id, cse_filing_id, versions=None):
    """Rule 1: the latest F5 run (under the versions) of a document this item retrieved successfully, or None."""
    row = _q(conn, f"""
        select r.id from financial_extraction_runs r
         where r.cse_filing_id = %(fid)s and {_VERSIONED}
           and exists (select 1 from backfill_retrieval_records rr where rr.item_id = %(item)s
                         and rr.outcome = 'succeeded' and rr.cleanup_status = 'deleted'
                         and rr.document_sha256 = r.document_sha256)
         order by r.recorded_at desc, r.id desc limit 1""",
             dict(versions or armed_versions(), fid=cse_filing_id, item=item_id), fetch="one")
    return None if row is None else str(row[0])


def paths_seen(conn, cse_filing_id):
    """The distinct non-null paths F1 recorded for the filing across every listing version it observed."""
    return sorted(r[0] for r in _q(conn, "select distinct raw_item ->> 'path' from report_filing_observations where "
                                         "cse_filing_id = %s and raw_item ->> 'path' is not null", (cse_filing_id,)))


def single_path_run(conn, cse_filing_id, path_sha256, versions=None):
    """Rule 2: the latest F5 run (under the versions) of a filing whose only path ever seen by F1 is the item's."""
    seen = paths_seen(conn, cse_filing_id)
    if len(seen) != 1 or keys.path_version(seen[0]) != path_sha256:
        return None
    row = _q(conn, f"select r.id from financial_extraction_runs r where r.cse_filing_id = %(fid)s and {_VERSIONED} "
                   f"order by r.recorded_at desc, r.id desc limit 1",
             dict(versions or armed_versions(), fid=cse_filing_id), fetch="one")
    return None if row is None else str(row[0])


def persisted_run(conn, item, versions=None):
    """The F5 run that proves `item` (a document work item) persisted under the armed versions, or None."""
    if item.get("path_sha256") is None:
        return None
    return (linked_run(conn, item["id"], item["cse_filing_id"], versions)
            or single_path_run(conn, item["cse_filing_id"], item["path_sha256"], versions))
