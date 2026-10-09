"""Safe diagnostic evidence and pure precision checks; no SQL or machine effects.

SQL, parameters, expressions and raw server log messages remain private. Original timed plans and later diagnostic executions carry distinct provenance;
a later replay is never a measurement of timed cold buffers.
"""

from itertools import pairwise
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[int | float, Field(ge=0, allow_inf_nan=False)]
Nat = Annotated[int, Field(strict=True, ge=0)]
ID = Annotated[str, Field(strict=True, min_length=1, max_length=255)]
Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True)


class Quantity(Evidence):
    precision: Literal["measured", "estimated", "unavailable"]
    value: Number | None
    uncertainty: Number | None
    meaning: ID

    @model_validator(mode="after")
    def precision_rule(self):
        if self.precision == "unavailable":
            if self.value is not None or self.uncertainty is not None:
                raise ValueError("unavailable evidence cannot have a value")
        elif self.value is None or self.uncertainty is None:
            raise ValueError("observed quantity requires explicit uncertainty")
        elif self.precision == "measured" and self.uncertainty != 0:
            raise ValueError("exact quantity cannot hide uncertainty")
        return self


class NodeFacts(Evidence):
    rows: Quantity
    loops: Number
    removed_filter: Quantity
    removed_recheck: Quantity
    removed_join: Quantity
    shared_hit_blocks: Quantity
    shared_read_blocks: Quantity


class PlanWorker(NodeFacts):
    worker_number: Nat


class PlanNode(NodeFacts):
    path: ID
    node_type: ID
    parallel_aware: bool | None = None
    workers_planned: Nat | None = None
    workers_launched: Nat | None = None
    workers: list[PlanWorker] | None = None


class ExecutionPlan(Evidence):
    query_id: ID
    raw_digest: Digest
    nodes: list[PlanNode]
    scan_rows: Quantity
    shared_hit_blocks: Quantity
    shared_read_blocks: Quantity
    duration_ns: Quantity


class Statistics(Evidence):
    reset_identity: ID
    deallocations: Nat
    database_id: Nat
    user_id: Nat
    query_id: ID
    calls: Nat
    rows: Nat
    total_exec_time_ms: Number
    shared_hit_blocks: Nat
    shared_read_blocks: Nat


class WaitObservation(Evidence):
    started_ns: Nat
    ended_ns: Nat
    backend_pid: Nat
    query_id: ID | None
    waiting_for_lock: bool
    blockers: list[Nat]
    ungranted_locks: Nat
    raw_digest: Digest


class TraceBinding(Evidence):
    backend_pid: Nat
    backend_start: ID
    session_id: ID
    transaction_id: Nat
    start_line: Nat
    end_line: Nat
    backend_records: Nat
    plan_query_ids: list[ID]
    sql_digest: Digest
    parameters_digest: Digest
    raw_digest: Digest
    start_marker_digest: Digest
    end_marker_digest: Digest


class StatementDiagnostic(Evidence):
    ordinal: Nat
    repository_query: Literal[
        "steps.count",
        "steps.page",
        "analysis.capture",
        "analysis.page",
        "analysis.charts",
        "analysis.points",
        "analysis.scores",
        "matrix.results",
    ]
    sql_digest: Digest
    parameters_digest: Digest
    backend_pid: Nat
    backend_start: ID
    database_id: Nat
    user_id: Nat
    started_ns: Nat
    ended_ns: Nat
    before: Statistics | None
    after: Statistics | None
    plan: ExecutionPlan | None
    nested_plans: list[ExecutionPlan]
    waits: list[WaitObservation]
    lock_wait_ns: Quantity
    instrumentation_ns: Quantity
    observer_elapsed_ns: Nat
    raw_digest: Digest
    errors: list[ID]
    statistics_attribution: Literal["single-call-delta", "aggregate-only"] = "single-call-delta"
    statistics_digest: Digest | None = None
    execution_started_ns: Nat | None = None
    execution_ended_ns: Nat | None = None
    plan_origin: Literal["original-timed-execution", "post-timing-diagnostic-execution"] = (
        "post-timing-diagnostic-execution"
    )
    trace_binding_digest: Digest | None = None
    trace_binding: TraceBinding | None = None
    source_started_ns: Nat
    source_ended_ns: Nat


class Query(Evidence):
    sample_id: ID
    action_id: ID
    operation: ID
    physical_window_id: ID
    clock_id: ID
    collected_ns: Nat
    ended_ns: Nat
    clone_id: ID
    timed_clone_id: ID
    timed_end_ns: Nat
    database_digest: Digest
    build_digest: Digest
    collection: Literal["pg_stat_statements+explain-analyze-buffers+pg_locks"]
    buffer_origin: Literal["post-timing-diagnostic-execution", "original-timed-execution"]
    statements: list[StatementDiagnostic]
    errors: list[ID]
    observer_clock_id: ID
    observer_started_ns: Nat
    observer_ended_ns: Nat
    boot_id: ID
    round_id: ID
    command_id: ID
    request_digest: Digest
    response_digest: Digest
    private_digest: Digest
    capture_digest: Digest


def validate_query(query, plan, sample, *, clock_id, origin, source):
    """Shared reader gate; failures remain retained records, never valid capacity."""
    if (
        query.sample_id != sample.sample_id
        or query.sample_id != plan.sample_id
        or query.action_id != plan.action_id
        or query.operation != plan.operation
        or query.physical_window_id != plan.physical_window_id
        or query.clock_id != clock_id
        or query.clone_id != origin.clone_id
        or query.timed_clone_id != origin.clone_id
        or query.timed_end_ns != sample.end_ns
        or query.boot_id != origin.boot_id
        or query.round_id != origin.round.round_id
        or query.capture_digest != source.repository_capture_digest
        or query.database_digest != source.database_identity_digest
        or query.build_digest != source.build_inventory_digest
        or query.observer_clock_id != source.repository_clock_id
        or not sample.end_ns <= query.collected_ns <= query.ended_ns
    ):
        raise ValueError("diagnostic sample/clone/clock/after-timing binding differs")
    from scripts.acceptance.capacity_io import canonical_digest

    derived_capture = canonical_digest(
        [
            {
                "repository_query": s.repository_query,
                "sql_digest": s.sql_digest,
                "parameters_digest": s.parameters_digest,
                "backend_pid": s.backend_pid,
                "started_ns": s.source_started_ns,
                "ended_ns": s.source_ended_ns,
            }
            for s in query.statements
        ]
    )
    if derived_capture != query.capture_digest:
        raise ValueError("original repository capture digest differs")
    if query.errors or not query.statements:
        raise ValueError("missing required diagnostic evidence")
    for ordinal, statement in enumerate(query.statements):
        if (
            statement.ordinal != ordinal
            or statement.errors
            or statement.plan is None
            or not query.observer_started_ns
            <= statement.started_ns
            <= statement.ended_ns
            <= query.observer_ended_ns
        ):
            raise ValueError("incomplete diagnostic statement evidence")
        if statement.statistics_attribution == "aggregate-only":
            if (
                statement.plan_origin != "original-timed-execution"
                or statement.statistics_digest is None
                or statement.trace_binding_digest is None
                or statement.before is not None
                or statement.after is not None
            ):
                raise ValueError("missing original bound-plan alternative to per-query statistics")
        else:
            validate_statistics(statement)
        if statement.repository_query.startswith("analysis.") and not statement.nested_plans:
            raise ValueError("wrapper plan cannot establish internal analysis work")
        if not statement.waits or statement.lock_wait_ns.precision == "unavailable":
            raise ValueError("missing actual bounded wait observations")
        if statement.lock_wait_ns.precision != "estimated":
            raise ValueError("snapshot wait cannot claim exact cumulative duration")
        if statement.plan_origin != query.buffer_origin:
            raise ValueError("diagnostic/timed buffer origin differs")
        validate_plan(statement.plan)
        for nested in statement.nested_plans:
            validate_plan(nested)
        if statement.plan_origin == "original-timed-execution":
            binding = statement.trace_binding
            if (
                binding is None
                or statement.trace_binding_digest
                != canonical_digest(binding.model_dump(mode="json"))
                or binding.backend_pid != statement.backend_pid
                or binding.backend_start != statement.backend_start
                or binding.sql_digest != statement.sql_digest
                or binding.parameters_digest != statement.parameters_digest
                or binding.end_line - binding.start_line - 1 != binding.backend_records
                or binding.backend_records < len(statement.nested_plans) + 1
                or binding.plan_query_ids
                != [p.query_id for p in statement.nested_plans] + [statement.plan.query_id]
                or statement.execution_started_ns != statement.source_started_ns
                or statement.execution_ended_ns != statement.source_ended_ns
            ):
                raise ValueError("missing/misbound original plan trace authority")
        if statement.execution_started_ns is None or statement.execution_ended_ns is None:
            raise ValueError("missing actual query execution bounds")
        if any(
            w.backend_pid != statement.backend_pid
            or not statement.started_ns <= w.started_ns <= w.ended_ns <= statement.ended_ns
            for w in statement.waits
        ):
            raise ValueError("wait observation backend/clock bounds differ")
        estimate = 0
        for left, right in pairwise(statement.waits):
            if left.ended_ns > right.started_ns:
                raise ValueError("overlapping wait observation bounds")
            if left.waiting_for_lock:
                estimate += max(
                    0,
                    min(statement.execution_ended_ns, right.started_ns)
                    - max(statement.execution_started_ns, left.ended_ns),
                )
        if statement.lock_wait_ns.value != estimate:
            raise ValueError("wait estimate differs from actual observation boundaries")
        if statement.execution_started_ns is not None and statement.execution_ended_ns is not None:
            duration = statement.execution_ended_ns - statement.execution_started_ns
            wait = statement.lock_wait_ns
            if (
                duration < 0
                or wait.value > duration
                or wait.uncertainty != max(wait.value, duration - wait.value)
            ):
                raise ValueError("wait sampling uncertainty differs")


def validate_plan(plan):
    if (
        not plan.nodes
        or plan.scan_rows.precision != "unavailable"
        or plan.shared_hit_blocks != plan.nodes[0].shared_hit_blocks
        or plan.shared_read_blocks != plan.nodes[0].shared_read_blocks
        or plan.shared_hit_blocks.precision != "measured"
        or plan.shared_read_blocks.precision != "measured"
        or plan.duration_ns.precision != "estimated"
        or plan.duration_ns.uncertainty != 500
    ):
        raise ValueError("missing/invalid plan aggregation precision")
    paths = set()
    for node in plan.nodes:
        if node.path in paths or (node.path != "0" and node.path.rsplit(".", 1)[0] not in paths):
            raise ValueError("invalid plan node tree")
        paths.add(node.path)
        workers = node.workers or []
        if len({w.worker_number for w in workers}) != len(workers):
            raise ValueError("duplicate plan worker identity")
        for facts in [node, *workers]:
            if facts.rows.precision != "estimated" or facts.loops != int(facts.loops):
                raise ValueError("missing actual node rows or nonintegral loops")
            for quantity in (
                facts.rows,
                facts.removed_filter,
                facts.removed_join,
                facts.removed_recheck,
            ):
                if quantity.precision != "unavailable" and (
                    quantity.precision != "estimated" or quantity.uncertainty != facts.loops * 0.5
                ):
                    raise ValueError("plan per-loop rounding precision differs")


def validate_statistics(statement):
    before, after = statement.before, statement.after
    if before is None or after is None:
        raise ValueError("missing per-query statistics")
    if (
        before.reset_identity != after.reset_identity
        or before.deallocations != after.deallocations
        or before.database_id != after.database_id
        or before.user_id != after.user_id
        or before.query_id != after.query_id
        or after.query_id != statement.plan.query_id
        or after.calls - before.calls != 1
        or before.database_id != statement.database_id
        or before.user_id != statement.user_id
    ):
        raise ValueError("uncertain/reset diagnostic attribution")
    for field in ("rows", "total_exec_time_ms", "shared_hit_blocks", "shared_read_blocks"):
        if getattr(after, field) < getattr(before, field):
            raise ValueError("diagnostic counters decreased")
