from copy import deepcopy

import pytest
from pydantic import BaseModel, ValidationError

from app.domain.execution.registry import (
    CommandRegistry,
    EventPayloads,
    EventRegistry,
    UnregisteredSchemaError,
)


class ExampleV1(BaseModel):
    value: int


class ExampleV2(BaseModel):
    value: int
    label: str


class ExampleInternalV1(BaseModel):
    secret_note: str = ""


class ExampleInternalV2(BaseModel):
    secret_note: str = ""
    annotated: bool = False


def upgrade_to_v2(payload: dict[str, object]) -> dict[str, object]:
    payload["label"] = "upcast"
    return payload


def upgrade_event_to_v2(payloads: EventPayloads) -> EventPayloads:
    return EventPayloads(
        public={**payloads.public, "label": "upcast"},
        internal={**payloads.internal, "annotated": True},
    )


def _payloads(value: int = 7, note: str = "n") -> EventPayloads:
    return EventPayloads(public={"value": value}, internal={"secret_note": note})


@pytest.mark.parametrize("registry_type", [EventRegistry, CommandRegistry])
def test_registry_rejects_duplicates_and_version_gaps(registry_type: type) -> None:
    registry = registry_type()
    registry.register("Example", 1, ExampleV1)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("Example", 1, ExampleV1)
    if registry_type is CommandRegistry:
        register_gap = lambda: registry.register("Example", 3, ExampleV2, upgrade_to_v2)  # noqa: E731
    else:
        register_gap = lambda: registry.register(  # noqa: E731
            "Example", 3, ExampleV2, upcast_from_previous=upgrade_event_to_v2
        )
    with pytest.raises(ValueError, match="version gap"):
        register_gap()


@pytest.mark.parametrize("registry_type", [EventRegistry, CommandRegistry])
def test_registry_accepts_an_explicit_greenfield_baseline_version(
    registry_type: type,
) -> None:
    registry = registry_type()

    registry.register("Example", 2, ExampleV2)

    assert registry.latest("Example").version == 2
    assert registry.latest_version("Example") == 2


@pytest.mark.parametrize("registry_type", [EventRegistry, CommandRegistry])
def test_registry_requires_an_upcaster_for_each_new_version(
    registry_type: type,
) -> None:
    registry = registry_type()
    registry.register("Example", 1, ExampleV1)

    with pytest.raises(ValueError, match="upcaster"):
        registry.register("Example", 2, ExampleV2)


def test_command_registry_resolves_latest_write_schema_and_purely_upcasts() -> None:
    registry = CommandRegistry()
    registry.register("Example", 1, ExampleV1)
    registry.register("Example", 2, ExampleV2, upgrade_to_v2)
    original = {"value": 7}
    untouched = deepcopy(original)

    latest = registry.latest("Example")
    version, payload = registry.upcast("Example", 1, original)

    assert latest.version == 2
    assert latest.model is ExampleV2
    assert version == 2
    assert payload == {"value": 7, "label": "upcast"}
    assert original == untouched
    assert ExampleV2.model_validate(payload).label == "upcast"


def test_event_registry_upcasts_public_and_internal_payloads_together() -> None:
    registry = EventRegistry()
    registry.register("Example", 1, ExampleV1, internal_model=ExampleInternalV1)
    registry.register(
        "Example",
        2,
        ExampleV2,
        internal_model=ExampleInternalV2,
        upcast_from_previous=upgrade_event_to_v2,
    )
    original = _payloads()

    version, payloads = registry.upcast("Example", 1, original)

    assert version == 2
    assert payloads.public == {"value": 7, "label": "upcast"}
    assert payloads.internal == {"secret_note": "n", "annotated": True}
    # The source is never mutated.
    assert original.public == {"value": 7}
    assert original.internal == {"secret_note": "n"}


def test_event_registry_write_side_guard_rejects_stale_version_and_bad_shape() -> None:
    registry = EventRegistry()
    registry.register("Example", 1, ExampleV1, internal_model=ExampleInternalV1)
    registry.register(
        "Example",
        2,
        ExampleV2,
        internal_model=ExampleInternalV2,
        upcast_from_previous=upgrade_event_to_v2,
    )

    registry.validate_new(
        "Example",
        2,
        EventPayloads(public={"value": 1, "label": "x"}, internal={}),
    )
    with pytest.raises(ValueError, match="latest schema version"):
        registry.validate_new("Example", 1, _payloads())
    with pytest.raises(ValidationError):
        registry.validate_new(
            "Example",
            2,
            EventPayloads(public={"value": 1}, internal={}),
        )


def test_event_registry_validates_internal_payload_shape() -> None:
    registry = EventRegistry()
    registry.register("Example", 1, ExampleV1, internal_model=ExampleInternalV1)

    with pytest.raises(ValidationError):
        registry.upcast(
            "Example",
            1,
            EventPayloads(public={"value": 1}, internal={"secret_note": 5}),
        )


@pytest.mark.parametrize("registry_type", [EventRegistry, CommandRegistry])
def test_registry_rejects_unknown_type_or_version(registry_type: type) -> None:
    registry = registry_type()
    registry.register("Example", 1, ExampleV1)
    empty = {} if registry_type is CommandRegistry else _payloads(note="")

    with pytest.raises(UnregisteredSchemaError, match="Unknown"):
        registry.latest("Unknown")
    with pytest.raises(UnregisteredSchemaError, match="version 0"):
        registry.upcast("Example", 0, empty)
    # A ValueError subclass: read-side consumers roll back instead of leaking
    # a KeyError past their exception handling.
    assert issubclass(UnregisteredSchemaError, ValueError)


def test_command_registry_validates_and_normalizes_payload_already_at_latest() -> None:
    registry = CommandRegistry()
    registry.register("Example", 1, ExampleV1)

    version, payload = registry.upcast("Example", 1, {"value": "7"})

    assert version == 1
    assert payload == {"value": 7}
    with pytest.raises(ValidationError):
        registry.upcast("Example", 1, {"not_value": 7})
