"""The read-boundary upcast pipeline shared by orchestrator and projectors."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import BaseModel

from app.domain.execution.events import StoredEvent
from app.domain.execution.registry import (
    EventPayloads,
    EventRegistry,
    UnregisteredSchemaError,
)
from app.infrastructure.execution.postgres_event_store import PostgresEventStore

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


class ThingV1(BaseModel):
    value: int


class ThingV2(BaseModel):
    value: int
    label: str


class ThingInternal(BaseModel):
    note: str = ""
    annotated: bool = False


def _registry() -> EventRegistry:
    registry = EventRegistry()
    registry.register("ThingHappened", 1, ThingV1, internal_model=ThingInternal)
    registry.register(
        "ThingHappened",
        2,
        ThingV2,
        internal_model=ThingInternal,
        upcast_from_previous=lambda payloads: EventPayloads(
            public={**payloads.public, "label": "upcast"},
            internal={**payloads.internal, "annotated": True},
        ),
    )
    return registry


def _event(
    *,
    stream_type: str = "run",
    event_type: str = "ThingHappened",
    version: int = 1,
) -> StoredEvent:
    return StoredEvent(
        position=1,
        event_id=UUID(int=1),
        stream_type=stream_type,
        stream_id="00000000-0000-0000-0000-000000000001",
        stream_version=1,
        event_type=event_type,
        event_schema_version=version,
        public_payload={"value": 7} if version == 1 else {"value": 7, "label": "x"},
        internal_payload={"note": "n"},
        secret_ref=None,
        owner_user_id="user-1",
        team_id=None,
        correlation_id=UUID(int=2),
        causation_id=None,
        occurred_at=NOW,
        prev_hash="0" * 64,
        event_hash="1" * 64,
    )


def _store(registry: EventRegistry | None) -> PostgresEventStore:
    registries = {"run": registry} if registry else None
    return PostgresEventStore(object(), event_registries=registries)  # type: ignore[arg-type]


def test_upcast_upgrades_public_and_internal_and_keeps_the_stored_hash() -> None:
    store = _store(_registry())

    (upcasted,) = store._upcast((_event(version=1),))

    assert upcasted.event_schema_version == 2
    assert upcasted.public_payload == {"value": 7, "label": "upcast"}
    assert upcasted.internal_payload == {"note": "n", "annotated": True}
    # Hashes cover the raw stored form; upcasting never rewrites them.
    assert upcasted.event_hash == "1" * 64


def test_latest_version_events_pass_through_untouched() -> None:
    store = _store(_registry())
    event = _event(version=2)

    (result,) = store._upcast((event,))

    assert result is event


def test_streams_without_a_registry_pass_through() -> None:
    store = _store(_registry())
    event = _event(stream_type="other")

    (result,) = store._upcast((event,))

    assert result is event


def test_unregistered_event_type_fails_closed() -> None:
    store = _store(_registry())

    with pytest.raises(UnregisteredSchemaError):
        store._upcast((_event(event_type="UnknownHappened"),))


def test_store_without_registries_never_upcasts() -> None:
    store = _store(None)
    event = _event(version=1)

    (result,) = store._upcast((event,))

    assert result is event
