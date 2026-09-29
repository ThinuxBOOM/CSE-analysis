"""
Security preflight of the scheduler's database role (run before every wake-up that writes; `verify` prints it).

The scheduler is the capture worker (cse_worker) and nothing more. On top of P2's own preflight (not a superuser, not
an owner, not a member of cse_owner / cse_migrator / cse_backup / pg_read_all_data, no UPDATE/DELETE/TRUNCATE on the
archive, no INSERT on block acknowledgements, append-only triggers enabled), it checks the P3 tables of migration
0014: the worker can read but never write the owner's settings, can only add items and item events, can only add and
update (heartbeat / release) its own lease rows, and can delete or truncate nothing; every guard trigger is enabled;
and trading_calendar is insert-only for the worker. [] means the P1/P2/P3 role model holds.
"""
from ..market_capture import runs as p2runs

# table -> (privileges the worker MUST have, privileges it must NOT have)
P3_TABLES = {
    "market_schedule_settings": (("SELECT",), ("INSERT", "UPDATE", "DELETE", "TRUNCATE")),
    "market_schedule_items": (("SELECT", "INSERT"), ("UPDATE", "DELETE", "TRUNCATE")),
    "market_schedule_item_events": (("SELECT", "INSERT"), ("UPDATE", "DELETE", "TRUNCATE")),
    "market_schedule_wakeups": (("SELECT", "INSERT", "UPDATE"), ("DELETE", "TRUNCATE")),
    "trading_calendar": (("SELECT", "INSERT"), ("UPDATE", "DELETE", "TRUNCATE")),
}
# table -> the 0014 triggers that must exist and be enabled (append-only, no-truncate, owner-only and state guards)
P3_TRIGGERS = {
    "market_schedule_settings": ("trg_mss_append_only", "trg_mss_no_truncate", "trg_mss_owner_only"),
    "market_schedule_items": ("trg_msi_append_only", "trg_msi_no_truncate"),
    "market_schedule_item_events": ("trg_msie_append_only", "trg_msie_no_truncate", "trg_msie_guard"),
    "market_schedule_wakeups": ("trg_msw_guard", "trg_msw_no_truncate"),
}


def _one(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
    conn.commit()
    return row


def problems(conn, expected_role="cse_worker"):
    import psycopg2
    out = list(p2runs.security_preflight(conn, expected_role))
    if any(p.startswith("connected as") for p in out):
        return out
    try:
        for table, (must, must_not) in P3_TABLES.items():
            if _one(conn, "select to_regclass(%s)", (f"public.{table}",))[0] is None:
                out.append(f"table {table} missing (migration 0014 not applied?)")
                continue
            for priv in must:
                if not _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{table}", priv))[0]:
                    out.append(f"{expected_role} lacks {priv} on {table}")
            for priv in must_not:
                if _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{table}", priv))[0]:
                    what = ("scheduler settings are an owner decision (G-1)" if table == "market_schedule_settings"
                            else "the scheduler's history is append-only")
                    out.append(f"{expected_role} has {priv} on {table}: {what}")
        for table, names in P3_TRIGGERS.items():
            if _one(conn, "select to_regclass(%s)", (f"public.{table}",))[0] is None:
                continue
            for name in names:
                enabled = _one(conn, "select coalesce(bool_and(tgenabled = 'O'), false) from pg_trigger where "
                                     "tgrelid = %s::regclass and tgname = %s and not tgisinternal",
                               (f"public.{table}", name))[0]
                if not enabled:
                    out.append(f"trigger {name} on {table} missing or disabled")
    except psycopg2.Error as exc:
        conn.rollback()
        out.append(f"cannot verify the scheduler tables: {type(exc).__name__}: {exc}".strip())
    return out
