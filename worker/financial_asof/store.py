"""
F8's only writes: its own configuration rows (migration 0017; docs/F8_DESIGN.md §13.1, §13.4). F8 writes no F1 to F6.4,
HB-1, HB-2 or HB-3 row.

- f8_configurations: content-addressed and insert-if-absent. The worker may register one. The database recomputes the
  id from the canonical JSON and checks the typed columns against it.
- f8_designations: the owner's decision of which configuration is canonical, append-only. It is accepted only through
  the owner path: the owner-delegation login cse_migrator acting as cse_owner for one INSERT, as F6.4's T9 and HB-1's
  arming. A trigger refuses the worker, backup and reader roles even if a grant were added by mistake.
"""
from .config import PURPOSE, F8Configuration
from .errors import Refused

OWNER_PATH_ROLE = "cse_migrator"


def register_configuration(conn, f6_configuration_id):
    """Register the F8 configuration of the implemented rule versions over `f6_configuration_id` (insert-if-absent).
    Returns (state, f8_configuration_id), with state 'registered' or 'already_present'."""
    cfg = F8Configuration(f6_configuration_id)
    text = cfg.canonical_json()
    with conn.cursor() as cur:
        cur.execute("insert into f8_configurations (f8_configuration_id, selection_version, availability_version, "
                    "supersession_version, knowledge_version, f6_configuration_id, configuration_json) "
                    "values (%s, %s, %s, %s, %s, %s, %s) on conflict (f8_configuration_id) do nothing "
                    "returning f8_configuration_id",
                    (cfg.f8_configuration_id, cfg.selection_version, cfg.availability_version,
                     cfg.supersession_version, cfg.knowledge_version, cfg.f6_configuration_id, text))
        inserted = cur.fetchone() is not None
        if not inserted:
            cur.execute("select configuration_json from f8_configurations where f8_configuration_id = %s",
                        (cfg.f8_configuration_id,))
            stored = cur.fetchone()[0]
            if stored != text:                          # impossible: the id is the hash of the text
                conn.rollback()
                raise RuntimeError(f"F8 configuration {cfg.f8_configuration_id}: stored JSON differs")
    conn.commit()
    return ("registered" if inserted else "already_present"), cfg.f8_configuration_id


def designate(conn, f8_configuration_id, note, os_user=None, purpose=PURPOSE):
    """The owner's designation of the canonical F8 configuration (owner path only). Returns the new row id."""
    with conn.cursor() as cur:
        cur.execute("select session_user")
        who = cur.fetchone()[0]
    conn.rollback()
    if who != OWNER_PATH_ROLE:
        raise Refused("owner_path_required", f"designating the canonical F8 configuration is an owner decision: "
                                             f"connected as {who!r}, it needs the owner path ({OWNER_PATH_ROLE} acting "
                                             f"as cse_owner)")
    try:
        with conn.cursor() as cur:
            cur.execute("set local role cse_owner")
            cur.execute("insert into f8_designations (purpose, f8_configuration_id, note, os_user) "
                        "values (%s, %s, %s, %s) returning id", (purpose, f8_configuration_id, note, os_user))
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception:
        conn.rollback()
        raise
