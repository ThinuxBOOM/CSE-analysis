"""
The work-item state machine (design sections 14.2 and 15.3).

This table is mirrored exactly by migration 0016's backfill_item_transitions, which the database's L3 guard enforces
on every event. A unit test compares it with the migration's text, a PostgreSQL test with the stored rows, and the
preflight checks the database against it before any write.

Actions:
    create   the item's first event
    claim    a CSE slice takes the item into flight (needs a live lease of that slice)
    record   the slice that holds the item records what happened, or a final decision without a request
    expire   a slice holding P2's lock found the item's lease dead: the item is abandoned
    promote  reconciliation from evidence rows (an F1 run, an F5 run, a canonical validation run, ...)
    resume   a blocked item returns to pending once the owner has acknowledged the block
    requeue  an explicit operator re-queue of a final item, with a reason (never automatic)
"""
CSE_KINDS = ("feed_window", "listing", "document")           # their requests need a live CSE slice (a lease)
DISCOVERY_KINDS = ("feed_window", "listing")
OTHER_KINDS = ("link_pass", "validate", "reconcile", "audit")  # no CSE request, so no lease and no in-flight state
KINDS = CSE_KINDS + OTHER_KINDS

ACTIONS = ("create", "claim", "record", "expire", "promote", "resume", "requeue")
IN_FLIGHT = ("requesting", "processing")
EXCLUSION_REASONS = ("out_of_window", "window_undetermined", "no_document", "invalid_path")
REQUEUE_REASON_MIN = 10
NONE = ""                                                    # "from" state of an item's first event

_DISCOVERY = (
    (NONE, "pending", "create"),
    ("pending", "requesting", "claim"), ("retry_wait", "requesting", "claim"),
    ("requesting", "succeeded", "record"), ("requesting", "partial", "record"), ("requesting", "failed", "record"),
    ("requesting", "retry_wait", "record"), ("requesting", "blocked", "record"), ("retry_wait", "failed", "record"),
    ("requesting", "abandoned", "expire"),
    ("abandoned", "pending", "promote"), ("abandoned", "succeeded", "promote"), ("abandoned", "partial", "promote"),
    ("blocked", "pending", "resume"),
    ("succeeded", "pending", "requeue"), ("partial", "pending", "requeue"), ("failed", "pending", "requeue"),
)
_DOCUMENT = (
    (NONE, "discovered", "create"), (NONE, "excluded", "create"),
    ("discovered", "pending", "promote"), ("discovered", "excluded", "record"),
    ("pending", "requesting", "claim"), ("retry_wait", "requesting", "claim"),
    ("requesting", "processing", "record"), ("requesting", "retry_wait", "record"),
    ("requesting", "retrieval_failed", "record"), ("requesting", "blocked", "record"),
    ("requesting", "cleanup_failed", "record"),
    ("processing", "persisted", "record"), ("processing", "consumer_failed", "record"),
    ("processing", "cleanup_failed", "record"), ("processing", "retry_wait", "record"),
    ("processing", "failed", "record"),
    ("retry_wait", "retrieval_failed", "record"), ("retry_wait", "failed", "record"),
    ("requesting", "abandoned", "expire"), ("processing", "abandoned", "expire"),
    ("abandoned", "pending", "promote"),
    ("discovered", "persisted", "promote"), ("pending", "persisted", "promote"), ("retry_wait", "persisted", "promote"),
    ("abandoned", "persisted", "promote"),
    ("persisted", "validated", "promote"), ("validated", "reconciled", "promote"),
    ("persisted", "needs_validation", "promote"), ("validated", "needs_validation", "promote"),
    ("reconciled", "needs_validation", "promote"), ("needs_validation", "validated", "promote"),
    ("blocked", "pending", "resume"),
    ("excluded", "pending", "requeue"), ("retrieval_failed", "pending", "requeue"),
    ("consumer_failed", "pending", "requeue"), ("cleanup_failed", "pending", "requeue"),
    ("failed", "pending", "requeue"),
)
_OTHER = (
    (NONE, "pending", "create"),
    ("pending", "succeeded", "record"), ("pending", "failed", "record"),
    ("succeeded", "pending", "requeue"), ("failed", "pending", "requeue"),
)

TRANSITIONS = frozenset(
    {(k, f, t, a) for k in DISCOVERY_KINDS for f, t, a in _DISCOVERY}
    | {("document", f, t, a) for f, t, a in _DOCUMENT}
    | {(k, f, t, a) for k in OTHER_KINDS for f, t, a in _OTHER})

STATES = {k: frozenset({t for kind, _, t, _ in TRANSITIONS if kind == k}) for k in KINDS}
ALL_STATES = frozenset().union(*STATES.values())
FIRST_STATES = {k: frozenset({t for kind, f, t, _ in TRANSITIONS if kind == k and f == NONE}) for k in KINDS}
# A final state leaves only by an explicit operator re-queue (or not at all).
FINAL = {k: frozenset(s for s in STATES[k]
                      if not any(kind == k and f == s and a != "requeue" for kind, f, _, a in TRANSITIONS))
         for k in KINDS}


class IllegalTransition(ValueError):
    pass


def allowed(kind, from_state, to_state, action):
    return (kind, from_state or NONE, to_state, action) in TRANSITIONS


def check(kind, from_state, to_state, action, reason=None):
    """Raise IllegalTransition unless the database would accept this transition's shape (the evidence it must name
    is the database guard's to check)."""
    if not allowed(kind, from_state, to_state, action):
        raise IllegalTransition(f"{kind}: {from_state or '(none)'} -> {to_state} ({action}) is not a legal transition")
    if to_state == "excluded" and reason not in EXCLUSION_REASONS:
        raise IllegalTransition(f"excluded needs one of {EXCLUSION_REASONS}, not {reason!r}")
    if action == "requeue" and len((reason or "").strip()) < REQUEUE_REASON_MIN:
        raise IllegalTransition(f"a re-queue is an explicit operator action and needs a reason of at least "
                                f"{REQUEUE_REASON_MIN} characters")


def next_states(kind, state):
    """{to_state: action} reachable from `state` (NONE for an item without events)."""
    return {t: a for k, f, t, a in TRANSITIONS if k == kind and f == (state or NONE)}


def rows():
    """The transitions as sorted rows, the shape of backfill_item_transitions."""
    return sorted(TRANSITIONS)
