"""Resolve opaque source identities against exact formal facts, without object I/O."""

from uuid import UUID

from sqlalchemy import select

from app.domain.execution.context_models import FormalContextSource
from app.domain.execution.store import verify_event_hashes
from app.infrastructure.execution.models import ExecutionEventORM
from app.infrastructure.execution.postgres_event_store import PostgresEventStore


class ContextSourceDescriptors:
    def __init__(self, session):
        self.session = session

    async def resolve(self, boundary, source):
        if (
            source.scope_key != boundary.scope_key
            or source.formal_position > boundary.through_position
            or source.kind not in {"user", "assistant"}
            or not source.source_id.startswith("formal:")
        ):
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")
        try:
            identity = UUID(source.source_id.removeprefix("formal:"))
        except ValueError as error:
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE") from error
        event = await self.session.scalar(
            select(ExecutionEventORM).where(
                ExecutionEventORM.owner_scope_key == boundary.scope_key,
                ExecutionEventORM.stream_type == "run",
                ExecutionEventORM.stream_id == str(source.run_id),
                ExecutionEventORM.event_id == identity,
                ExecutionEventORM.position == source.formal_position,
            )
        )
        if event is None:
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")
        verify_event_hashes((PostgresEventStore._to_stored(event),))
        if source.kind == "user" and event.event_type == "RunCreated":
            semantic = event.internal_payload.get("semantic_payload", {})
            ref, digest = semantic.get("input_ref"), semantic.get("input_digest")
        elif source.kind == "assistant" and event.event_type == "RunCompleted":
            ref = event.public_payload.get("result_ref")
            digest = ref.rsplit("/", 1)[-1].removesuffix(".json") if isinstance(ref, str) else None
        else:
            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
        if not isinstance(ref, str) or digest != source.digest:
            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
        return FormalContextSource(
            source_id=source.source_id,
            run_id=source.run_id,
            formal_position=source.formal_position,
            event_hash=event.event_hash,
            kind=source.kind,
            object_ref=ref,
            object_digest=digest,
        )
