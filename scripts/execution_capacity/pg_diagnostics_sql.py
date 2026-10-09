"""Fixed PG16 SQL acquisition; caller supplies already authorized live connections.

The observer connection must be separate/autocommit. No extension installation,
statistics reset, privilege change, role switch, or service startup is performed.
"""

import itertools

from scripts.acceptance.capacity_diagnostics import Quantity, Statistics
from scripts.execution_capacity.pg_diagnostics_plan import unavailable

IDENTITY = """SELECT current_database() AS database_name,current_user AS database_user,
 (SELECT system_identifier::text FROM pg_control_system()) AS database_system_identifier,
 current_setting('server_version_num') AS server_version,
 (SELECT array_agg(version_num ORDER BY version_num) FROM alembic_version) AS migrations,
 (SELECT oid FROM pg_database WHERE datname=current_database()) AS database_id,
 (SELECT oid FROM pg_roles WHERE rolname=current_user) AS user_id,
 pg_backend_pid() AS pid, a.backend_start::text AS backend_start,
 to_hex(floor(extract(epoch FROM a.backend_start))::bigint)||'.'||to_hex(a.pid) AS session_id,
 txid_current() AS txid,pg_current_logfile('jsonlog') AS logfile
 FROM pg_stat_activity a WHERE a.pid=pg_backend_pid()"""
CAPABILITIES = """SELECT
 (SELECT extversion FROM pg_extension WHERE extname='pg_stat_statements') AS extension_version,
 (SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
 WHERE e.extname='pg_stat_statements') AS extension_schema,
 (SELECT array_agg(attname::text ORDER BY attname) FROM pg_attribute
 WHERE attrelid=to_regclass('public.pg_stat_statements') AND attnum>0 AND NOT attisdropped) AS columns,
 current_setting('pg_stat_statements.track',true) AS track,
 current_setting('compute_query_id',true) AS compute_query_id,
 current_setting('auto_explain.log_min_duration',true) AS log_min_duration,
 current_setting('auto_explain.log_analyze',true) AS log_analyze,
 current_setting('auto_explain.log_buffers',true) AS log_buffers,
 current_setting('auto_explain.log_verbose',true) AS log_verbose,
 current_setting('auto_explain.log_nested_statements',true) AS log_nested_statements,
 current_setting('auto_explain.sample_rate',true) AS sample_rate,
 current_setting('auto_explain.log_format',true) AS log_format,
 current_setting('auto_explain.log_parameter_max_length',true) AS log_parameter_max_length,
 current_setting('logging_collector') AS logging_collector,
 current_setting('log_destination') AS log_destination"""
STATS_INFO = "SELECT stats_reset::text AS reset,dealloc FROM public.pg_stat_statements_info"
STATS = """SELECT queryid,calls,rows,total_exec_time,shared_blks_hit,shared_blks_read
 FROM public.pg_stat_statements WHERE dbid=$1 AND userid=$2 AND toplevel
 ORDER BY queryid LIMIT 10001"""
WAIT = """SELECT a.pid,a.backend_start::text AS backend_start,a.query_id::text AS query_id,
 a.wait_event_type,a.wait_event,a.state,pg_blocking_pids(a.pid) AS blockers,
 (SELECT count(*) FROM pg_locks l WHERE l.pid=a.pid AND NOT l.granted) AS ungranted_locks,
 (SELECT coalesce(jsonb_agg(jsonb_build_object('locktype',l.locktype,'mode',l.mode,
 'granted',l.granted,'waitstart',l.waitstart,'relation',l.relation,'transactionid',l.transactionid)), '[]')
 FROM pg_locks l WHERE l.pid=a.pid) AS locks
 FROM pg_stat_activity a WHERE a.pid=$1"""


def validate_capabilities(identity, capabilities, inventory):
    inventory.require_complete()
    for key in ("database_name", "database_system_identifier", "migrations", "server_version"):
        if identity.get(key) != inventory.database.get(key):
            raise ValueError("actual diagnostic database identity differs")
    if not 160000 <= int(identity["server_version"]) < 170000:
        raise ValueError("unsupported PostgreSQL diagnostic/log version")
    expected = {
        "extension_schema": "public",
        "extension_version": "1.10",
        "track": "top",
        "log_min_duration": "0",
        "log_analyze": "on",
        "log_buffers": "on",
        "log_verbose": "on",
        "log_nested_statements": "on",
        "sample_rate": "1",
        "log_format": "json",
        "log_parameter_max_length": "0",
        "logging_collector": "on",
    }
    if any(capabilities.get(k) != v for k, v in expected.items()):
        raise ValueError("missing installed diagnostic capability or safe logging settings")
    columns = {
        "userid",
        "dbid",
        "toplevel",
        "queryid",
        "calls",
        "rows",
        "total_exec_time",
        "shared_blks_hit",
        "shared_blks_read",
    }
    if (
        not columns <= set(capabilities.get("columns") or [])
        or capabilities.get("compute_query_id") not in {"on", "auto"}
        or "jsonlog" not in (capabilities.get("log_destination") or "").split(",")
        or not identity.get("logfile")
    ):
        raise ValueError("missing diagnostic columns/query identity/jsonlog")


async def statistics(observer, database_id, user_id):
    # Observer must not hold a long transaction/cache across the two snapshots.
    if observer.is_in_transaction():
        raise ValueError("diagnostic observer must use independent autocommit reads")
    info = dict(await observer.fetchrow(STATS_INFO))
    rows = [dict(r) for r in await observer.fetch(STATS, database_id, user_id)]
    final = dict(await observer.fetchrow(STATS_INFO))
    if info != final or len(rows) > 10000 or len({r["queryid"] for r in rows}) != len(rows):
        raise ValueError("reset/eviction/overflow while collecting raw statistics")
    return {**info, "rows": rows}


class StatisticsAttributionUnavailable(ValueError):
    """Valid snapshots cannot establish a unique single-call delta."""


def validated_statistics(snapshot, *, database_id, user_id):
    """Validate every retained row before deciding whether attribution is possible."""
    if (
        type(snapshot["reset"]) is not str
        or not 1 <= len(snapshot["reset"]) <= 255
        or type(snapshot["dealloc"]) is not int
        or snapshot["dealloc"] < 0
        or type(snapshot["rows"]) is not list
        or len(snapshot["rows"]) > 10000
    ):
        raise ValueError("invalid statistics snapshot/reset metadata")
    result = {}
    for row in snapshot["rows"]:
        if type(row["queryid"]) is not int or not -(2**63) <= row["queryid"] < 2**63:
            raise ValueError("invalid statistics query identity")
        query_id = str(row["queryid"])
        if query_id in result:
            raise ValueError("duplicate statistics query identity")
        result[query_id] = Statistics(
            reset_identity=snapshot["reset"],
            deallocations=snapshot["dealloc"],
            database_id=database_id,
            user_id=user_id,
            query_id=query_id,
            calls=row["calls"],
            rows=row["rows"],
            total_exec_time_ms=row["total_exec_time"],
            shared_hit_blocks=row["shared_blks_hit"],
            shared_read_blocks=row["shared_blks_read"],
        )
    return result


def attributed_statistics(before, after, query_id, *, database_id, user_id):
    # Invalid facts must never be confused with valid missing/concurrent facts.
    first = validated_statistics(before, database_id=database_id, user_id=user_id)
    last = validated_statistics(after, database_id=database_id, user_id=user_id)
    if before["reset"] != after["reset"] or before["dealloc"] != after["dealloc"]:
        raise ValueError("statistics reset or eviction invalidates attribution")
    for key in first.keys() & last.keys():
        if any(
            getattr(last[key], field) < getattr(first[key], field)
            for field in (
                "calls",
                "rows",
                "total_exec_time_ms",
                "shared_hit_blocks",
                "shared_read_blocks",
            )
        ):
            raise ValueError("statistics counters decreased")
    if query_id not in first or query_id not in last:
        raise StatisticsAttributionUnavailable("missing actual query statistics row")
    a, b = first[query_id], last[query_id]
    if b.calls - a.calls != 1:
        raise StatisticsAttributionUnavailable("non-unique actual query call delta")
    return a, b


def wait_quantity(rows, started_ns, ended_ns):
    if not rows:
        return unavailable("no bounded backend wait observations")
    duration = ended_ns - started_ns
    if duration < 0 or any(r.started_ns > r.ended_ns for r in rows):
        raise ValueError("invalid wait observation clock order")
    estimate = 0
    for row, following in itertools.pairwise(rows):
        if row.ended_ns > following.started_ns:
            raise ValueError("overlapping wait observation boundaries")
        if row.waiting_for_lock:
            estimate += max(0, min(ended_ns, following.started_ns) - max(started_ns, row.ended_ns))
    return Quantity(
        precision="estimated",
        value=estimate,
        uncertainty=max(estimate, duration - estimate),
        meaning="sampled lock-wait rectangle estimate; conservative true range 0..execution duration",
    )
