"""NS-7 preflight: the 0022 tables exist, the connected role has exactly its privileges, and every guard trigger is
enabled. Read-only."""
TABLES = ("ns_sources", "ns_source_events", "ns_fetch_attempts", "ns_fetch_events", "ns_ingestion_runs",
          "ns_archive_imports", "ns_articles", "ns_article_observations", "ns_entity_aliases", "ns_alias_ends",
          "ns_configurations", "ns_designations", "ns_article_results", "ns_snapshots", "ns_snapshot_rows")
WORKER_INSERT = ("ns_source_events", "ns_fetch_attempts", "ns_fetch_events", "ns_ingestion_runs", "ns_articles",
                 "ns_article_observations", "ns_configurations", "ns_article_results", "ns_snapshots",
                 "ns_snapshot_rows")
GUARDS = {"ns_sources": ["trg_nss_guard"], "ns_source_events": ["trg_nse_guard"],
          "ns_archive_imports": ["trg_nsi_owner"], "ns_entity_aliases": ["trg_nsl_owner"],
          "ns_alias_ends": ["trg_nsle_owner"], "ns_designations": ["trg_nsd_owner"], "ns_articles": ["trg_nsart_guard"],
          "ns_configurations": ["trg_nsc_guard"]}


def triggers(table):
    return GUARDS.get(table, []) + [f"trg_{table}_append_only", f"trg_{table}_stamp", f"trg_{table}_no_truncate"]


def problems(conn):
    out = []
    try:
        with conn.cursor() as cur:
            for t in TABLES:
                cur.execute("select to_regclass(%s)", (f"public.{t}",))
                if cur.fetchone()[0] is None:
                    out.append(f"relation {t} missing (migration 0022 not applied?)")
                    continue
                must = ("SELECT", "INSERT") if t in WORKER_INSERT else ("SELECT",)
                for priv in must:
                    cur.execute("select has_table_privilege(current_user, %s, %s)", (f"public.{t}", priv))
                    if not cur.fetchone()[0]:
                        out.append(f"{t}: {priv} missing")
                for priv in (("UPDATE", "DELETE", "TRUNCATE") if t in WORKER_INSERT else
                             ("INSERT", "UPDATE", "DELETE", "TRUNCATE")):
                    cur.execute("select has_table_privilege(current_user, %s, %s)", (f"public.{t}", priv))
                    if cur.fetchone()[0]:
                        out.append(f"{t}: {priv} must not be granted")
                for trg in triggers(t):
                    cur.execute("select tgenabled from pg_trigger where tgname = %s", (trg,))
                    row = cur.fetchone()
                    if row is None or row[0] == "D":
                        out.append(f"{t}: trigger {trg} missing or disabled")
    finally:
        conn.rollback()
    return out
