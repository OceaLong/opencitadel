"""Read-only formal/public/object joins inside the inventory SQL snapshot."""

from contextlib import asynccontextmanager
from hashlib import sha256
from uuid import UUID

from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.persistence import PersistedFacts
from sqlalchemy import select

from app.application.ports.execution_view import StepStorage
from app.application.services.execution_view_service import assemble_step, assemble_view
from app.domain.execution.run import RunAggregate
from app.domain.execution.serialization import canonical_state_hash
from app.domain.execution.store import verify_stream
from app.domain.models.playback import PlaybackBoundary
from app.infrastructure.execution import postgres_execution_view as view_storage
from app.infrastructure.execution.models import ExecutionRunProjectionORM
from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView


def validate_standard_counts(index, fact):
    from scripts.seed_execution_visualization import event_count

    steps = 3331 if index < 10 else 31 if index < 1000 else 32
    if (fact["status"], fact["formal_events"], fact["visible_steps"]) != (
        "completed",
        event_count(index),
        steps,
    ):
        raise ValueError("standard per-Run source distribution differs")


def replay_projection(run_id, events, projected):
    if not events:
        raise ValueError("missing retained source")
    verify_stream(events)
    aggregate = RunAggregate()
    state = aggregate.initial_state(str(run_id))
    for event in events:
        if event.stream_type != "run" or event.stream_id != str(run_id):
            raise ValueError("foreign retained source")
        state = aggregate.evolve(state, event)
    if (
        projected is None
        or projected.stream_version != len(events)
        or projected.state_hash != canonical_state_hash(state)
        or projected.last_event_hash != events[-1].event_hash
    ):
        raise ValueError("canonical formal projection differs")
    if (
        state.status.value not in {"completed", "failed", "cancelled"}
        or state.active_activity_ids
        or not projected.terminal
    ):
        raise ValueError("source Run is not settled terminal")
    return state


def validate_activity_parent(task, events):
    """Bind task object references to retained hash-verified formal facts."""
    payloads = [
        event
        for event in events
        if str(event.public_payload.get("activity_id")) == str(task["activity_id"])
    ]
    requests = [
        event.public_payload for event in payloads if event.event_type == "ActivityRequested"
    ]
    if len(requests) != 1 or any(
        requests[0].get(field) != task[column]
        for field, column in (
            ("generation", "request_generation"),
            ("input_ref", "request_ref"),
            ("input_digest", "request_digest"),
        )
    ):
        raise ValueError("object lacks exact formal activity parent request")
    if task["status"] == "succeeded":
        completed = [
            event.public_payload for event in payloads if event.event_type == "ActivityCompleted"
        ]
        if len(completed) != 1 or any(
            completed[0].get(field) != task[column]
            for field, column in (
                ("generation", "request_generation"),
                ("result_ref", "result_ref"),
                ("claim_generation", "claim_generation"),
            )
        ):
            raise ValueError("object lacks exact formal activity parent settlement")


class SnapshotFacts(PersistedFacts):
    """Reuse existing signed configuration and source readers on one owned read snapshot."""

    def __init__(self, session, journal, scope, *, evidence=None):
        super().__init__(None, None, journal, scope, None, evidence=evidence)
        self.db = session
        self.validated_parents = {}

    async def events(self, run_id):
        parent = self.validated_parents.get(str(run_id))
        if parent is None:
            return await super().events(run_id)
        if parent["scope"] != self.scope_key:
            raise ValueError("actual retained parent scope differs")
        return await self.retained_events(run_id)

    @asynccontextmanager
    async def session(self):
        yield self.db


async def persisted_steps(query, port, scope, boundary, generation, replay):
    """Read actual current storage; never create cuts or accept shadow fallback."""
    params = {"scope": view_storage.scope_key(scope), "run": boundary.run_id}
    if generation == "live":
        return await query.rows(
            "steps",
            "SELECT * FROM execution_view_steps WHERE scope_key=:scope AND run_id=:run ORDER BY step_id",
            params,
        )
    rows = await query.rows(
        "shadow-run",
        """SELECT * FROM execution_view_shadow_runs
        WHERE scope_key=:scope AND run_id=:run AND generation=CAST(:generation AS uuid)""",
        {**params, "generation": generation},
    )
    token, _, _ = await view_storage._coverage(query.db, scope, boundary, evidence=port.evidence)
    validate_shadow_run(rows, boundary, generation, params["scope"], token, replay)
    table, where, params = port._step_source(scope, boundary, StepStorage("shadow", generation))
    rows = await query.rows(
        "shadow-steps", f"SELECT * FROM {table} WHERE {where} ORDER BY step_id", params
    )
    orders = await view_storage._step_orders(query.db, scope, boundary, evidence=port.evidence)
    return validate_shadow_steps(rows, boundary, generation, params["scope"], orders, replay)


def validate_shadow_run(rows, boundary, generation, scope_key, token, replay):
    if len(rows) != 1:
        raise ValueError("active persisted shadow Run missing or duplicated")
    shadow = rows[0]
    if (
        str(shadow["generation"]) != generation
        or shadow["run_id"] != boundary.run_id
        or shadow["scope_key"] != scope_key
        or shadow["observed_order"] != boundary.observed_order
        or PlaybackBoundary.model_validate(shadow["boundary"]) != boundary
        or shadow["coverage_token"] != token
        or shadow["missing_intervals"]
        or shadow["state"] != replay.state
    ):
        raise ValueError("active persisted shadow Run differs from retained source boundary/state")


def validate_shadow_steps(rows, boundary, generation, scope_key, orders, replay):
    actual = {row["step_id"]: row["payload"] for row in rows}
    if (
        len(actual) != len(rows)
        or actual != replay.state.get("step", {})
        or any(
            str(row["generation"]) != generation
            or row["run_id"] != boundary.run_id
            or row["scope_key"] != scope_key
            or row["observed_order"] != orders.get(row["step_id"], 0)
            for row in rows
        )
    ):
        raise ValueError("active persisted shadow step set/order/parity differs")
    return [{**row["payload"], "step_id": row["step_id"]} for row in rows]


class LiveRunInputs:
    """Finite source reads used by the shared run predicate; no alternate policy."""

    def __init__(self, query, facts, signing_secret, storage):
        self.query, self.facts = query, facts
        self.signing_secret, self.storage = signing_secret, storage
        self.scope, self.scope_key = facts.scope, facts.scope_key
        self.evidence = getattr(facts, "evidence", None)
        self.port = PostgresExecutionView(
            session_factory=None, authorization=None, evidence=self.evidence
        )

    async def events(self, run_id):
        events = await self.facts.events(run_id)
        if self.evidence is not None:
            self.evidence.reserve_state(len(events), bytes_per_item=1024)
        return events

    async def projection(self, run_id):
        value = await self.query.db.scalar(
            select(ExecutionRunProjectionORM).where(ExecutionRunProjectionORM.run_id == run_id)
        )
        if self.evidence is not None:
            self.evidence.retain("projection", value)
        return value

    async def configuration(self, run_id, *, purpose):
        return await self.facts.configuration(
            run_id, self.signing_secret, purpose=purpose, record=False
        )

    async def capture(self, run_id):
        return await self.port.capture_run(self.query.db, self.scope, run_id)

    async def generation(self):
        return await self.port.active_generation(self.query.db, self.scope)

    async def restore(self, boundary):
        return await self.port.restore(self.query.db, self.scope, boundary, "live")

    async def steps(self, boundary, generation, restored):
        return await persisted_steps(
            self.query, self.port, self.scope, boundary, generation, restored
        )

    async def rows(self, name, sql, params):
        return await self.query.rows(name, sql, params)

    def activity_parent(self, identity):
        return self.facts.journal.get("activity", identity)

    async def get_bytes(self, key):
        return await self.storage.get_bytes(key)


async def read_run(query, facts, run_id, kind, signing_secret, storage):
    inputs = LiveRunInputs(query, facts, signing_secret, storage)
    if inputs.evidence is None:
        return await replay_run(inputs, run_id, kind)
    with inputs.evidence.run_inputs(
        run_id=run_id, scope=inputs.scope_key, kind=kind, objects=storage.originals
    ):
        return await replay_run(inputs, run_id, kind)


async def replay_run(inputs, run_id, kind):
    """Compare retained formal replay, public replay, persisted steps and content parents.

    Uses public DTO assembly but deliberately does not call writable HTTP read-cut
    creation. This is persisted-public parity, not an HTTP/browser observation.
    """
    events = await inputs.events(UUID(run_id))
    projected = await inputs.projection(UUID(run_id))
    state = replay_projection(run_id, events, projected)
    purpose = kind if kind in {"evaluation_subject", "evaluation_judge"} else "production"
    config = await inputs.configuration(UUID(run_id), purpose=purpose)
    boundary = await inputs.capture(UUID(run_id))
    generation = await inputs.generation()
    if boundary.formal_position != events[-1].position:
        raise ValueError("persisted public formal watermark differs")
    # Force independent retained journal replay. Active shadow storage is compared
    # below; restore's shadow/fallback optimization cannot prove its existence.
    restored = await inputs.restore(boundary)
    if restored.missing_intervals:
        raise ValueError("persisted public replay coverage incomplete")
    public = assemble_view(
        scope=inputs.scope,
        boundary=boundary,
        state=restored.state,
        latest_available=boundary.observed_at,
        missing_intervals=restored.missing_intervals,
    )
    if public.run.purpose != purpose:
        raise ValueError("public purpose differs from actual admission")
    if (
        public.run.configuration is None
        or public.run.configuration.configuration_revision != config
    ):
        raise ValueError("public configuration differs from signed actual admission")
    if public.run.status.value != state.status.value:
        raise ValueError("source/public terminal status differs")
    params = {"run": UUID(run_id), "scope": inputs.scope_key}
    rows = await inputs.steps(boundary, generation, restored)
    persisted = {
        row["step_id"]: assemble_step(boundary, row["step_id"], row).model_dump(mode="json")
        for row in rows
    }
    replayed = {step.step_id: step.model_dump(mode="json") for step in public.steps}
    if len(persisted) != len(rows) or len(replayed) != len(public.steps) or persisted != replayed:
        raise ValueError("persisted public step set/parity differs")
    bindings = await inputs.rows(
        "content",
        """SELECT b.step_id,b.phase,b.content_id,b.event_id,b.formal_position,c.activity_id,c.generation,c.claim_generation,c.content_digest,c.byte_length
      FROM execution_content_bindings b JOIN execution_public_content c ON c.content_id=b.content_id AND c.scope_key=b.scope_key
      WHERE b.scope_key=:scope AND b.run_id=:run ORDER BY b.step_id,b.phase,b.content_id""",
        params,
    )
    event_map = {str(e.event_id): e for e in events}
    content = {}
    for row in bindings:
        key = (row["step_id"], row["phase"])
        event = event_map.get(str(row["event_id"]))
        payload = {} if event is None else event.public_payload
        if (
            key in content
            or event is None
            or event.position != row["formal_position"]
            or str(payload.get("activity_id")) != str(row["activity_id"])
            or payload.get("generation", 0) != row["generation"]
            or payload.get("claim_generation") != row["claim_generation"]
        ):
            raise ValueError("public content lacks exact retained event/claim parent")
        content[key] = str(row["content_id"])
    for step in public.steps:
        for phase, ref in [("input", step.input_ref), ("output", step.output_ref)]:
            if ref is not None and content.get((step.step_id, phase)) != ref.content_id:
                raise ValueError("public content identity differs from formal binding")
    tasks = await inputs.rows(
        "tasks",
        "SELECT * FROM execution_activity_tasks WHERE aggregate_type='run' AND aggregate_id=:id ORDER BY activity_id",
        {"id": run_id},
    )
    requested = {str(a) for a, _, _ in state.requested_activities}
    if len(tasks) != len(requested) or {str(t["activity_id"]) for t in tasks} != requested:
        raise ValueError("complete activity source set differs")
    refs = []
    for task in tasks:
        validate_activity_parent(task, events)
        if (
            task["owner_user_id"] != inputs.scope.user_id
            or task["team_id"] is not None
            or task["status"] not in {"succeeded", "failed", "cancelled"}
            or task["claimed_by"] is not None
            or task["claim_deadline"] is not None
        ):
            raise ValueError("activity unresolved or foreign")
        prior = inputs.activity_parent(task["activity_id"])
        if prior is not None and str(prior["body"]["run_id"]) != run_id:
            raise ValueError("historical activity parent differs")
        for phase in ("request", "result"):
            key = task[phase + "_ref"]
            expected = task[phase + "_digest"]
            if key is None:
                if phase == "result" and task["status"] == "succeeded":
                    raise ValueError("successful activity result object missing")
                continue
            if not expected or not key.startswith("execution/"):
                raise ValueError("unknown object parent")
            data = await inputs.get_bytes(key)
            actual = sha256(data).hexdigest()
            if expected.removeprefix("sha256:") != actual:
                raise ValueError("retained object bytes differ")
            refs.append(
                {
                    "run_id": run_id,
                    "activity_id": str(task["activity_id"]),
                    "generation": task["request_generation"],
                    "claim_generation": task["claim_generation"],
                    "phase": phase,
                    "key": key,
                    "sha256": actual,
                    "size_bytes": len(data),
                }
            )
    observations = await inputs.rows(
        "observations",
        "SELECT observed_order,projection_revision,source_kind,source_identity,projector_version FROM execution_view_observations WHERE scope_key=:scope AND run_id=:run ORDER BY observed_order",
        params,
    )
    formal_ids = [
        str(row["source_identity"]) for row in observations if row["source_kind"] == "formal"
    ]
    if len(formal_ids) != len(events) or set(formal_ids) != set(event_map):
        raise ValueError("complete public formal observation identities differ")
    if len({row["observed_order"] for row in observations}) != len(observations):
        raise ValueError("duplicate public observation order")
    if (
        not observations
        or observations[-1]["observed_order"] != boundary.observed_order
        or any(r["projector_version"] != boundary.projector_version for r in observations)
    ):
        raise ValueError("public observation head/version differs")
    return {
        "run_id": run_id,
        "status": state.status.value,
        "formal_events": len(events),
        "observations": len(observations),
        "visible_steps": len(public.steps),
        "configuration_id": config,
        "state_hash": projected.state_hash,
        "last_event_hash": projected.last_event_hash,
        "generation": generation,
        "projector_version": boundary.projector_version,
        "boundary": boundary.model_dump(mode="json"),
        "public_digest": canonical_digest(public.model_dump(mode="json")),
    }, refs
