"""
Document items from F6 evidence (design sections 14.2 and 15.3: "a canonical T1 exists -> validated"; "promotions to
terminal states ... may run at any wake-up"). HB-5 reads, decides, then appends one HB-1 event per promotion in a
transaction of its own; the database's guard re-checks every claim against F6.4's own views.

    persisted | needs_validation -> validated   the item's F5 run has its canonical validation run (M4): the event
                                                names the run, that validation run and the F6 job that wrote it
    validated -> reconciled                     that validation run contributes an input to a CURRENT record of the
                                                designated configuration (F6.4's view financial_fact_provenance, the
                                                same test as the funnel's stage 9)
    persisted | validated | reconciled -> needs_validation
                                                the run HAD a validation run, but none is canonical now: late evidence
                                                changed its issuer decision or its upload time (M4, section 7.4 step 5)

A persisted item whose run was never validated stays persisted: it is pending validation, not re-entering. The item's
F5 run is the one its latest event names (every state of the persisted family names one).
"""
from ..financial_backfill import store
from ..financial_truth_store import jobs, selection
from . import RULE_VERSION, preflight as checks
from .snapshot import read_only

FAMILY = ("persisted", "validated", "reconciled", "needs_validation")

ITEMS_SQL = """
    select s.item_id::text, s.state, r.f5_run_id::text
      from backfill_item_state s
      join lateral (select e.f5_run_id from backfill_item_events e where e.item_id = s.item_id
                       and e.f5_run_id is not null order by e.seq desc limit 1) r on true
     where s.item_kind = 'document' and s.state = any(%s)
     order by s.item_id"""


def decide(state, *, canonical, ever_validated, reconciled):
    """Pure: the promotions (in order) of one item. canonical: its run's canonical validation run key or None;
    ever_validated: the run has any validation run; reconciled: `canonical` is in a current designated record."""
    if canonical is None:
        return ["needs_validation"] if ever_validated and state != "needs_validation" else []
    out = []
    if state in ("persisted", "needs_validation"):
        out.append("validated")
        state = "validated"
    if state == "validated" and reconciled:
        out.append("reconciled")
    return out


def plan(conn):
    """[(item_id, f5_run_id, [(to_state, refs)])] from one read-only snapshot."""
    out = []
    with read_only(conn) as cur:
        designated = jobs.designated_configuration(cur)
        cur.execute(ITEMS_SQL, (list(FAMILY),))
        for item_id, state, run in cur.fetchall():
            canonical = selection.canonical_validation_run(cur, run)
            cur.execute("select exists (select 1 from financial_validation_runs where f5_run_id = %s)", (run,))
            ever = cur.fetchone()[0]
            job = reconciled = None
            if canonical is not None:
                cur.execute("select job_id::text from financial_validation_runs where validation_run_key = %s",
                            (canonical,))
                job = cur.fetchone()[0]
                if designated is not None:
                    cur.execute("select exists (select 1 from financial_fact_provenance where configuration_id = %s "
                                "and validation_run_key = %s)", (designated, canonical))
                    reconciled = cur.fetchone()[0]
            steps = []
            for to in decide(state, canonical=canonical, ever_validated=ever, reconciled=bool(reconciled)):
                refs = {"f5_run_id": run}
                if to in ("validated", "reconciled"):
                    refs["validation_run_key"] = canonical
                if to == "validated" and job is not None:
                    refs["f6_job_id"] = job
                steps.append((to, refs))
            if steps:
                out.append((item_id, run, steps))
    return out


def promote_documents(conn, *, wakeup_id=None, preflight=None):
    """Apply every due promotion. Returns {to_state: count}."""
    checks.require(conn, preflight)
    counts = {}
    for item_id, _run, steps in plan(conn):
        for to, refs in steps:
            store.append_event(conn, item_id, to, "promote", details={"rule": RULE_VERSION}, wakeup_id=wakeup_id,
                               **refs)
            counts[to] = counts.get(to, 0) + 1
    return counts
