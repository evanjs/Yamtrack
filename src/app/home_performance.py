"""Opt-in, parameter-safe profiling for home-page database work."""

import logging
import time
from contextlib import contextmanager
from hashlib import sha256

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)


def _query_plan(query):
    """Return a short query-plan summary without filter values."""
    sql = query["sql"]
    if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
        return "not-a-select"

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"{connection.ops.explain_query_prefix()} {sql}",
                query["params"],
            )
            rows = cursor.fetchall()
    except Exception as error:  # noqa: BLE001
        return f"unavailable:{type(error).__name__}"

    details = [str(row[-1]) for row in rows]
    if connection.vendor == "sqlite":
        return " | ".join(details)[:1200] or "empty"

    node_markers = (
        "SCAN ",
        "SORT",
        "LIMIT",
        "AGGREGATE",
        "WINDOW",
        "NESTED LOOP",
        "HASH JOIN",
        "BITMAP",
        "SUBQUERY",
        "MATERIALIZE",
    )
    excluded_markers = (
        "FILTER:",
        "INDEX COND:",
        "RECHECK COND:",
        "SORT KEY:",
        "OUTPUT:",
        "JOIN FILTER:",
    )
    plan_nodes = [
        line.strip()
        for line in details
        if any(marker in line.upper() for marker in node_markers)
        and not line.lstrip().upper().startswith(excluded_markers)
    ]
    return " | ".join(plan_nodes)[:1200] or "no-plan-nodes"


@contextmanager
def profile_home_queries(
    status,
    media_type,
    *,
    sort_by,
    items_limit,
    hide_unreleased,
):
    """Log timings and the slowest query plan when explicitly enabled."""
    if not getattr(settings, "HOME_PERFORMANCE_PROFILE", False):
        yield
        return

    sort_value = getattr(sort_by, "value", sort_by)
    database_limited = sort_value == "upcoming" and items_limit is not None
    queries = []
    started = time.perf_counter()

    def record_query(execute, sql, params, many, context):
        query_started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            queries.append(
                {
                    "duration_ms": (time.perf_counter() - query_started) * 1000,
                    "sql": sql,
                    "params": params,
                },
            )

    try:
        with connection.execute_wrapper(record_query):
            yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        db_ms = sum(query["duration_ms"] for query in queries)
        slowest = max(queries, key=lambda query: query["duration_ms"], default=None)
        if slowest:
            sql_template = " ".join(str(slowest["sql"]).split())
            sql_fingerprint = sha256(sql_template.encode()).hexdigest()[:12]
            sql_template = sql_template[:2400]
            plan = _query_plan(slowest)
            slowest_ms = slowest["duration_ms"]
        else:
            sql_fingerprint = "none"
            sql_template = "none"
            plan = "none"
            slowest_ms = 0

        logger.info(
            "[home-profile] status=%s media_type=%s sort_by=%s items_limit=%s "
            "hide_unreleased=%s db_limited=%s elapsed_ms=%.1f query_count=%d "
            "db_ms=%.1f slowest_ms=%.1f sql_sha=%s slowest_sql=%s plan=%s",
            status,
            media_type,
            sort_value,
            items_limit,
            hide_unreleased,
            database_limited,
            elapsed_ms,
            len(queries),
            db_ms,
            slowest_ms,
            sql_fingerprint,
            sql_template,
            plan,
        )
