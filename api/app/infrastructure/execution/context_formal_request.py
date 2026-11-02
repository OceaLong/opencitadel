"""Recover immutable Activity inputs after disposable queue rows have been purged."""

from sqlalchemy import select

from app.domain.execution.store import verify_event_hashes
from app.infrastructure.execution.models import ExecutionEventORM
from app.infrastructure.execution.postgres_event_store import PostgresEventStore


async def context_activity_request(session, state, activity_id, generation, activity_type):
    if (activity_id, activity_type, generation) not in state.requested_activities:
        raise ValueError("CONTEXT_SOURCE_REQUEST_UNAVAILABLE")
    key = f"team:{state.team_id}" if state.team_id else f"user:{state.owner_user_id}"
    event = ExecutionEventORM
    rows = (
        await session.scalars(
            select(event)
            .where(
                event.owner_scope_key == key,
                event.stream_type == "run",
                event.stream_id == str(state.run_id),
                event.event_type == "ActivityRequested",
                event.stream_version <= state.stream_version,
                event.public_payload["activity_id"].astext == str(activity_id),
                event.public_payload["generation"].astext == str(generation),
            )
            .limit(2)
        )
    ).all()
    if len(rows) != 1:
        raise ValueError("CONTEXT_SOURCE_REQUEST_UNAVAILABLE")
    stored = PostgresEventStore._to_stored(rows[0])
    verify_event_hashes((stored,))
    public = rows[0].public_payload
    pin = state.definition_pin
    if (
        pin is None
        or public.get("activity_type") != activity_type
        or public.get("definition_digest") != pin.manifest_digest
        or public.get("handler_version") != state.definition_manifest.handler_version(activity_type)
        or public.get("input_digest") != state.semantic_payload.get("input_digest")
        or public.get("input_ref") != state.semantic_payload.get("input_ref")
    ):
        raise ValueError("CONTEXT_SOURCE_REQUEST_UNAVAILABLE")
    payload = rows[0].internal_payload.get("input_payload")
    if not isinstance(payload, dict):
        raise ValueError("CONTEXT_SOURCE_REQUEST_UNAVAILABLE")  # noqa: TRY004 - stable public code
    return payload
