"""Finite replay of one actual run-read bundle; never a database or Result emulator."""

from types import SimpleNamespace
from uuid import UUID

from scripts.execution_capacity.evidence_owner import FAMILIES
from scripts.execution_capacity.inventory_readback import replay_run
from scripts.execution_capacity.inventory_sql import plain, validate_preflight
from scripts.execution_capacity.observer_sql_scope import _literals
from scripts.execution_capacity.persistence import PersistedFacts, verify_admission_configuration
from sqlalchemy import inspect, select, text

from app.domain.execution.store import verify_stream
from app.domain.models.playback import PlaybackBoundary
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution import postgres_execution_view as view
from app.infrastructure.execution import postgres_playback as playback
from app.infrastructure.execution.models import ExecutionEventORM, ExecutionRunProjectionORM
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.models.execution_view import (
    ExecutionPlaybackCheckpointORM,
    ExecutionRunViewORM,
    ExecutionViewObservationORM,
)

_MODELS = frozenset(
    {
        ExecutionEventORM,
        ExecutionRunProjectionORM,
        ExecutionRunViewORM,
        ExecutionPlaybackCheckpointORM,
        ExecutionViewObservationORM,
    }
)


def _model(kind, raw):
    if raw is None:
        return None
    if kind not in _MODELS or set(raw) != {c.key for c in inspect(kind).column_attrs}:
        raise ValueError("closed original model column coverage differs")
    return kind(**raw)


def _literal(function, prefix):
    values = [value for value in _literals(function) if value.lstrip().startswith(prefix)]
    if len(values) != 1:
        raise ValueError("private replay query definition is ambiguous")
    return text(values[0])


class RetainedRunInputs:
    def __init__(self, bundle, operands, sql, objects, *, signing_secret, budget):
        if (
            set(bundle) != {"identity", "ranges", "sql", "objects", "error"}
            or bundle["error"] is not None
        ):
            raise ValueError("incomplete original run-read bundle")
        if set(bundle["identity"]) != {"run_id", "scope", "kind"} or set(
            bundle["ranges"]
        ) != FAMILIES - {"run-input"}:
            raise ValueError("original run-read coverage differs")
        self.budget, self.signing_secret = budget, signing_secret
        self.identity = bundle["identity"]
        self.scope_key = self.identity["scope"]
        if not self.scope_key.startswith("user:"):
            raise ValueError("original run trusted personal scope required")
        self.scope = OwnerScope.personal(self.scope_key.removeprefix("user:"))
        self.families, self.positions = {}, {}
        for family, bounds in bundle["ranges"].items():
            self.families[family] = self._slice(operands[family], bounds)
            self.positions[family] = 0
        self.sql = self._slice(sql, bundle["sql"])
        self.sql_start = bundle["sql"][0]
        self.journal_start = bundle["ranges"]["journal-read"][0]
        self.sql_position = 0
        self.objects = self._slice(objects, bundle["objects"])
        self.object_position = 0
        self.uow = self.snapshot = None
        UUID(self.identity["run_id"])
        if not isinstance(signing_secret, str) or not signing_secret:
            raise ValueError("original requester verification material required")

    def _slice(self, rows, bounds):
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or any(type(n) is not int for n in bounds)
            or not 0 <= bounds[0] <= bounds[1] <= len(rows)
        ):
            raise ValueError("original run-read bounds differ")
        self.budget.reserve(8 * (bounds[1] - bounds[0]), rows=bounds[1] - bounds[0])
        return rows[bounds[0] : bounds[1]]

    def _take(self, family):
        index = self.positions[family]
        if index == len(self.families[family]):
            raise ValueError("required original run operand absent")
        self.positions[family] += 1
        return self.families[family][index]

    def _dispatch(self, statement, params=None):
        if self.sql_position == len(self.sql):
            raise ValueError("original run SQL observation absent")
        row = self.sql[self.sql_position]
        self.sql_position += 1
        compiled = statement.compile()
        if (
            row.get("statement") != str(compiled)
            or row.get("parameters") != (params or {})
            or row.get("bound_parameters") != compiled.params
            or row.get("dispatched") is not True
            or row.get("error") is not None
            or not 0 < row["start_ns"] <= row["end_ns"]
        ):
            raise ValueError("original run SQL statement/parameters/dispatch differs")
        if self.uow is None:
            self.uow, self.snapshot = row["uow"], row["snapshot"]
        if (row["uow"], row["snapshot"]) != (self.uow, self.snapshot) or row["snapshot"] != row[
            "preflight"
        ]["snapshot"]:
            raise ValueError("original run readonly snapshot differs")
        validate_preflight(row["preflight"], self.budget)
        return row

    def _parent(self, kind, key):
        row = self._take("journal-read")
        if row["sequence"] != self.journal_start + self.positions["journal-read"]:
            raise ValueError("original journal read sequence differs")
        if (row["operation"], row["kind"], row["key"], row["error"], row["sql_boundary"]) != (
            "get",
            kind,
            str(key),
            None,
            self.sql_start + self.sql_position,
        ):
            raise ValueError("original run journal read identity/order differs")
        return row["value"]

    async def events(self, run_id):
        # Capacity fixture runs consume their immutable original parent before
        # the event query; evaluation/admission parents are checked by context.
        if (
            self.identity["kind"]
            not in {"evaluation_subject", "evaluation_judge", "live", "admission"}
            and self._parent("run", run_id) is None
        ):
            raise ValueError("original run parent missing")
        self._dispatch(
            select(ExecutionEventORM)
            .where(
                ExecutionEventORM.stream_type == "run", ExecutionEventORM.stream_id == str(run_id)
            )
            .order_by(ExecutionEventORM.stream_version.asc())
        )
        rows = self._take("event-source")
        self.budget.reserve(len(rows) * 1024, rows=len(rows))
        events = tuple(
            PostgresEventStore._to_stored(_model(ExecutionEventORM, row)) for row in rows
        )
        verify_stream(events)
        events = PostgresEventStore(None)._upcast(events)
        if any(
            event.owner_user_id != self.scope.user_id or event.team_id is not None
            for event in events
        ):
            raise ValueError("actual source parent belongs to another scope")
        return events

    async def projection(self, run_id):
        self._dispatch(
            select(ExecutionRunProjectionORM).where(ExecutionRunProjectionORM.run_id == run_id)
        )
        return _model(ExecutionRunProjectionORM, self._take("projection"))

    async def configuration(self, run_id, *, purpose):
        self._dispatch(
            _literal(PersistedFacts.configuration, "SELECT id,body,purpose"),
            {"scope": self.scope_key, "run": run_id},
        )
        return verify_admission_configuration(
            self._take("signed-configuration"),
            scope=self.scope,
            run_id=run_id,
            purpose=purpose,
            signing_secret=self.signing_secret,
        )

    async def capture(self, run_id):
        self._dispatch(
            select(ExecutionRunViewORM).where(
                ExecutionRunViewORM.scope_key == self.scope_key,
                ExecutionRunViewORM.run_id == run_id,
            )
        )
        row = _model(ExecutionRunViewORM, self._take("view-capture"))
        if (
            row is None
            or row.run_id != run_id
            or row.scope_key != self.scope_key
            or not row.observed_order
            or row.as_of is None
        ):
            raise ValueError("original captured Run boundary missing")
        return view.boundary_of(row)

    async def generation(self):
        self._dispatch(
            _literal(view.PostgresExecutionView.active_generation, "SELECT active_generation"),
            {"scope": self.scope_key},
        )
        value = self._take("view-generation")
        return str(value) if value else "live"

    def _boundary(self, boundary):
        self._dispatch(
            select(
                ExecutionViewObservationORM.formal_position,
                ExecutionViewObservationORM.progress_position,
                ExecutionViewObservationORM.projection_revision,
                ExecutionViewObservationORM.observed_at,
            ).where(
                ExecutionViewObservationORM.run_id == boundary.run_id,
                ExecutionViewObservationORM.scope_key == self.scope_key,
                ExecutionViewObservationORM.projector_version == boundary.projector_version,
                ExecutionViewObservationORM.observed_order == boundary.observed_order,
            )
        )
        original = self._take("playback-boundary-observation")
        if (
            original["identity"]
            != {
                "run_id": boundary.run_id,
                "scope_key": self.scope_key,
                "projector_version": boundary.projector_version,
                "observed_order": boundary.observed_order,
            }
            or original["error"] is not None
        ):
            raise ValueError("original boundary query identity/error differs")
        row = original["row"]
        if row is not None and set(row) != {
            "formal_position",
            "progress_position",
            "projection_revision",
            "observed_at",
        }:
            raise ValueError("original boundary result columns differ")
        playback.check_playback_boundary(None if row is None else SimpleNamespace(**row), boundary)

    def _coverage(self, boundary):
        params = {
            "scope": self.scope_key,
            "run": boundary.run_id,
            "cut": boundary.observed_order,
            "formal": boundary.formal_position,
            "progress": boundary.progress_position,
            "version": boundary.projector_version,
        }
        for prefix in ("SELECT count(*) AS count,", "SELECT completeness->", "SELECT state_ref->"):
            self._dispatch(_literal(view._coverage, prefix), params)
        row = self._take("view-coverage")
        if PlaybackBoundary.model_validate(row["boundary"]) != boundary:
            raise ValueError("original coverage boundary differs")
        return view.coverage_from_originals(
            boundary, SimpleNamespace(**row["aggregate"]), row["current"], row["checkpoints"]
        )

    async def restore(self, boundary):
        self._boundary(boundary)
        self._coverage(boundary)
        if PlaybackBoundary.model_validate(self._take("playback-boundary")) != boundary:
            raise ValueError("original playback boundary differs")
        self.budget.reserve(boundary.observed_order * 256, rows=boundary.observed_order)
        self._boundary(boundary)
        self._dispatch(
            select(ExecutionRunViewORM).where(
                ExecutionRunViewORM.run_id == boundary.run_id,
                ExecutionRunViewORM.scope_key == self.scope_key,
            )
        )
        run = _model(ExecutionRunViewORM, self._take("playback-run"))
        if (
            run is None
            or run.run_id != boundary.run_id
            or run.scope_key != self.scope_key
            or run.projector_version != boundary.projector_version
        ):
            raise playback.PlaybackUnavailable(
                "playback source journal version is unavailable in trusted scope"
            )
        filters = (
            ExecutionPlaybackCheckpointORM.run_id == boundary.run_id,
            ExecutionPlaybackCheckpointORM.scope_key == self.scope_key,
            ExecutionPlaybackCheckpointORM.projector_version == boundary.projector_version,
            ExecutionPlaybackCheckpointORM.observed_order <= boundary.observed_order,
            ExecutionPlaybackCheckpointORM.formal_position <= boundary.formal_position,
            ExecutionPlaybackCheckpointORM.progress_position <= boundary.progress_position,
        )
        self._dispatch(
            select(ExecutionPlaybackCheckpointORM)
            .where(*filters)
            .order_by(ExecutionPlaybackCheckpointORM.observed_order.desc())
            .limit(1)
        )
        checkpoint = _model(ExecutionPlaybackCheckpointORM, self._take("playback-checkpoint"))
        if checkpoint is not None and (
            checkpoint.run_id != boundary.run_id
            or checkpoint.scope_key != self.scope_key
            or checkpoint.projector_version != boundary.projector_version
            or not 0 <= checkpoint.observed_order <= boundary.observed_order
            or checkpoint.formal_position > boundary.formal_position
            or checkpoint.progress_position > boundary.progress_position
        ):
            raise ValueError("original checkpoint is outside scoped cut")
        self._dispatch(
            select(ExecutionPlaybackCheckpointORM.state_ref["missing_intervals"]).where(*filters)
        )
        missing = self._take("playback-missing")
        observations = (
            ExecutionViewObservationORM.run_id == boundary.run_id,
            ExecutionViewObservationORM.scope_key == self.scope_key,
            ExecutionViewObservationORM.projector_version == boundary.projector_version,
        )
        cut = (
            ExecutionViewObservationORM.observed_order <= boundary.observed_order,
            ExecutionViewObservationORM.formal_position <= boundary.formal_position,
            ExecutionViewObservationORM.progress_position <= boundary.progress_position,
        )
        self._dispatch(
            select(ExecutionViewObservationORM.observed_order)
            .where(*observations, *cut)
            .order_by(ExecutionViewObservationORM.observed_order)
        )
        orders = self._take("playback-orders")
        prefix = playback.playback_prefix(boundary, run, checkpoint, missing, orders)
        self._dispatch(
            select(ExecutionViewObservationORM)
            .where(*observations, ExecutionViewObservationORM.observed_order > prefix[1], *cut)
            .order_by(ExecutionViewObservationORM.observed_order)
        )
        rows = [
            _model(ExecutionViewObservationORM, row) for row in self._take("playback-observations")
        ]
        if any(
            row.run_id != boundary.run_id
            or row.scope_key != self.scope_key
            or row.projector_version != boundary.projector_version
            or not prefix[1] < row.observed_order <= boundary.observed_order
            or row.formal_position > boundary.formal_position
            or row.progress_position > boundary.progress_position
            for row in rows
        ):
            raise ValueError("original playback suffix is outside scoped cut")
        self.budget.reserve(
            sum(len(row.public_payload.get("facts", [])) for row in rows) * 1024, rows=len(rows)
        )
        return playback.finish_playback(boundary, prefix, rows)

    async def rows(self, name, sql, params):
        self._dispatch(text(sql), params)
        original = self._take("query-rows")
        if (
            original["name"],
            original["statement"],
            original["parameters"],
            original["sql_index"],
        ) != (name, sql, params, self.sql_start + self.sql_position - 1):
            raise ValueError("original named query identity/order differs")
        read = original["read"]
        if (
            read.get("error") is not None
            or read["parameters"] != plain(params)
            or read["rows"] != len(original["rows"])
            or read["preflight"] != self.sql[self.sql_position - 1]["preflight"]
        ):
            raise ValueError("original query read boundary differs")
        return original["rows"]

    async def steps(self, boundary, generation, restored):
        from scripts.execution_capacity.inventory_readback import persisted_steps

        params = {"scope": self.scope_key, "run": boundary.run_id}
        if generation == "live":
            return await self.rows(
                "steps",
                _literal(persisted_steps, "SELECT * FROM execution_view_steps").text,
                params,
            )
        from scripts.execution_capacity.inventory_readback import (
            validate_shadow_run,
            validate_shadow_steps,
        )

        from app.application.ports.execution_view import StepStorage

        rows = await self.rows(
            "shadow-run",
            _literal(persisted_steps, "SELECT * FROM execution_view_shadow_runs").text,
            {**params, "generation": generation},
        )
        token, _, _ = self._coverage(boundary)
        validate_shadow_run(rows, boundary, generation, self.scope_key, token, restored)
        port = view.PostgresExecutionView(session_factory=None, authorization=None)
        table, where, params = port._step_source(
            self.scope, boundary, StepStorage("shadow", generation)
        )
        rows = await self.rows(
            "shadow-steps", f"SELECT * FROM {table} WHERE {where} ORDER BY step_id", params
        )
        self._dispatch(
            _literal(view._step_orders, "SELECT f->>'id'"),
            {
                "scope": self.scope_key,
                "run": boundary.run_id,
                "version": boundary.projector_version,
                "cut": boundary.observed_order,
            },
        )
        original = self._take("view-step-orders")
        if PlaybackBoundary.model_validate(original["boundary"]) != boundary:
            raise ValueError("original step-order boundary differs")
        orders = {row["step_id"]: row["last_order"] for row in original["rows"]}
        return validate_shadow_steps(rows, boundary, generation, self.scope_key, orders, restored)

    def activity_parent(self, identity):
        return self._parent("activity", identity)

    async def get_bytes(self, key):
        if self.object_position == len(self.objects):
            raise ValueError("original object bytes absent")
        row = self.objects[self.object_position]
        self.object_position += 1
        if (
            row["key"] != key
            or row["error"] is not None
            or type(row["data"]) is not bytes
            or not 0 < row["start_ns"] <= row["end_ns"]
        ):
            raise ValueError("original object read identity/error differs")
        self.budget.reserve(len(row["data"]) * 64, rows=1)
        return row["data"]

    async def replay(self):
        result = await replay_run(self, self.identity["run_id"], self.identity["kind"])
        if (
            self.sql_position != len(self.sql)
            or self.object_position != len(self.objects)
            or any(self.positions[key] != len(rows) for key, rows in self.families.items())
        ):
            raise ValueError("unconsumed original run inputs")
        return result
