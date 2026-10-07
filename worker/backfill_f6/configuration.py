"""
The backfill's reconciliation configuration (design section 10.3: "register_configuration(configuration_from_present_runs)
over the backfill's single version tuple. It runs in HB-S3, once the pilot's first F5 runs exist, because
configuration_from_present_runs refuses when there are none. The owner designates it canonical (F6.4's owner path,
unchanged)").

HB-5 registers; it never designates. F6.4's own configuration_from_present_runs accepts every F3 / F4 / F5 version
tuple present among the persisted F5 runs, so it is registered only while exactly ONE tuple is present and it is the
armed one (section 10.2: the stage versions and the pinned Poppler identity, as HB-4's evidence rule reads them).
Anything else is the owner's to decide, never chosen here.
"""
from ..backfill_documents import evidence
from ..financial_truth_store import jobs
from . import preflight as checks
from .errors import F6Refused
from .snapshot import read_only


def backfill_tuple(versions=None):
    """The armed (F3, F4, F5) version tuple, in F6.3's configuration shape."""
    v = versions or evidence.armed_versions()
    return ((v["classifier_version"], v["text_extractor"]), (v["word_extractor"], v["f4_extractor_version"]),
            (v["builder_version"], v["mapper_version"], v["vocabulary_version"]))


def tuple_refusals(configuration, versions=None):
    """[] when the configuration accepts exactly the armed tuple and nothing else."""
    f3, f4, f5 = backfill_tuple(versions)
    got = (tuple(configuration.accepted_f3), tuple(configuration.accepted_f4), tuple(configuration.accepted_f5))
    if got != ((f3,), (f4,), (f5,)):
        return [("version_tuples", f"the persisted F5 runs carry the version tuples {got}, not exactly the armed "
                                   f"{(f3, f4, f5)}: which configuration to register is the owner's decision")]
    return []


def register(conn, *, versions=None, preflight=None):
    """Register the backfill configuration (insert-if-absent, F6.4's own). Returns its state and id and the
    designation in force; the owner designates it (or not) through F6.4's owner path."""
    checks.require(conn, preflight)
    with read_only(conn) as cur:
        try:
            configuration = jobs.configuration_from_present_runs(cur)
        except jobs.JobRefused as exc:
            raise F6Refused([("no_runs", str(exc))]) from None
    refusals = tuple_refusals(configuration, versions)
    if refusals:
        raise F6Refused(refusals)
    state, cid = jobs.register_configuration(conn, configuration)
    with read_only(conn) as cur:
        designated = jobs.designated_configuration(cur)
    return {"state": state, "configuration_id": cid, "designated": designated, "designated_is_this": designated == cid}


def designated(conn):
    with read_only(conn) as cur:
        return jobs.designated_configuration(cur)
