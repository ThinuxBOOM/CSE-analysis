"""
Environment configuration. No secrets live in this file or anywhere else in
the codebase — everything comes from environment variables, per the project's
$0/no-hardcoded-credentials constraint.
"""
import os


class ConfigError(Exception):
    pass


def get_database_url() -> str:
    """
    Postgres connection string for the restricted `cse_worker` role — NOT the
    Supabase service_role key. This is deliberate: the worker should connect
    with a role that structurally cannot UPDATE/DELETE raw_market_observations
    or raw_index_observations (see Stage A's migration comment block).
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ConfigError(
            "DATABASE_URL is not set. This must be a direct Postgres connection "
            "string for the restricted cse_worker role, e.g.:\n"
            "  postgresql://cse_worker:<password>@<host>:5432/postgres\n"
            "Set it as an environment variable or GitHub Actions secret — never "
            "hardcode it, and never use the Supabase service_role key here."
        )
    return url


def get_user_agent() -> str:
    return os.environ.get(
        "CSE_USER_AGENT",
        "cse-research-tool/1.0 (personal research project, read-only, polite pacing)",
    )


def get_request_delay_seconds() -> float:
    return float(os.environ.get("CSE_REQUEST_DELAY_SECONDS", "1.0"))
