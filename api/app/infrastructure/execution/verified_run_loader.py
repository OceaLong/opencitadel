"""One snapshot/anchor/tail protocol for command and physical admission."""

from app.application.ports.verified_run import VerifiedRun
from app.domain.execution.aggregate import replay
from app.domain.execution.definition import DefinitionIntegrityError
from app.domain.execution.run import RunAggregate, RunState
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.execution.store import (
    SnapshotAnchorMismatchError,
    StreamOwnerScopeMismatchError,
    StreamRef,
)
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.execution.postgres_snapshot_store import PostgresSnapshotStore
from app.infrastructure.observability.execution_metrics import record_replay_failure


class VerifiedAggregateLoader:
    def __init__(self, events, snapshots):
        self.events = events
        self.snapshots = snapshots

    async def load(
        self,
        aggregate,
        stream_type,
        stream_id,
        *,
        owner_scope=None,
        required_pin=None,
        at_version=None,
    ):
        if at_version is not None and at_version < 0:
            raise ValueError("at_version must not be negative")
        origin = None
        if hasattr(self.events, "load_origin") and at_version != 0:
            origin = await self.events.load_origin(stream_type, stream_id)
            if (
                origin is not None
                and owner_scope is not None
                and (origin.owner_user_id, origin.team_id) != owner_scope
            ):
                raise StreamOwnerScopeMismatchError(
                    stream=StreamRef(stream_type=stream_type, stream_id=stream_id)
                )
        snapshot_kwargs = {}
        if at_version is not None:
            snapshot_kwargs["max_stream_version"] = at_version
        snapshot = await self.snapshots.load(
            stream_type,
            stream_id,
            state_type=aggregate.state_type,
            serializer_version=aggregate.snapshot_serializer_version,
            **snapshot_kwargs,
        )
        if snapshot is not None and hasattr(self.events, "load_origin") and origin is None:
            await self._invalidate(stream_type, stream_id, snapshot.stream_version)
            snapshot = None
        if snapshot is not None and origin is not None:
            initial = aggregate.evolve(aggregate.initial_state(stream_id), origin)
            state = snapshot.state
            if isinstance(state, RunState) and (
                str(state.run_id) != stream_id
                or (state.owner_user_id, state.team_id) != (origin.owner_user_id, origin.team_id)
                or state.definition_pin != initial.definition_pin
                or state.definition_manifest != initial.definition_manifest
                or state.policy_snapshot != initial.policy_snapshot
                or state.family != initial.family
                or state.source_entity_type != initial.source_entity_type
                or state.source_entity_id != initial.source_entity_id
            ):
                await self._invalidate(stream_type, stream_id, snapshot.stream_version)
                snapshot = None
        event_kwargs = {}
        if at_version is not None:
            event_kwargs["through_version"] = at_version
        if snapshot is not None:
            try:
                events = await self.events.load_stream(
                    stream_type,
                    stream_id,
                    after_version=snapshot.stream_version,
                    expected_previous_hash=snapshot.last_event_hash,
                    **event_kwargs,
                )
            except SnapshotAnchorMismatchError:
                await self._invalidate(stream_type, stream_id, snapshot.stream_version)
                snapshot = None
        if snapshot is None:
            events = await self.events.load_stream(stream_type, stream_id, **event_kwargs)
        result = replay(aggregate, events, snapshot=snapshot, stream_id=stream_id)
        if at_version is not None and result.stream_version != at_version:
            raise ValueError("requested formal stream version is unavailable")
        if result.stream_version and owner_scope is not None:
            state = result.state
            if (
                hasattr(state, "owner_user_id")
                and (state.owner_user_id, state.team_id) != owner_scope
            ):
                raise StreamOwnerScopeMismatchError(
                    stream=StreamRef(stream_type=stream_type, stream_id=stream_id)
                )
        if required_pin is not None and result.state.definition_pin != required_pin:
            raise DefinitionIntegrityError("recovered Run definition differs from admitted pin")
        return VerifiedRun(
            replay=result,
            snapshot_version=snapshot.stream_version if snapshot else None,
            tail_count=len(events),
            snapshot_bytes=len(canonical_json_bytes(snapshot.state.model_dump(mode="json")))
            if snapshot
            else 0,
        )

    async def _invalidate(self, stream_type, stream_id, version):
        record_replay_failure("snapshot_hash_mismatch")
        await self.snapshots.delete_version(stream_type, stream_id, version)


class PostgresVerifiedRunLoader:
    def __init__(self, session):
        self.aggregate = RunAggregate()
        self.loader = VerifiedAggregateLoader(
            PostgresEventStore(session, event_registries={"run": self.aggregate.event_registry}),
            PostgresSnapshotStore(session),
        )

    async def load(self, run_id, *, owner_scope, required_pin=None, at_version=None):
        return await self.loader.load(
            self.aggregate,
            "run",
            str(run_id),
            owner_scope=owner_scope,
            required_pin=required_pin,
            at_version=at_version,
        )
