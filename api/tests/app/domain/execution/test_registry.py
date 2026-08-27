from copy import deepcopy

import pytest
from pydantic import BaseModel, ValidationError

from app.domain.execution.registry import CommandRegistry, EventRegistry


class ExampleV1(BaseModel):
    value: int


class ExampleV2(BaseModel):
    value: int
    label: str


def upgrade_to_v2(payload: dict[str, object]) -> dict[str, object]:
    payload["label"] = "upcast"
    return payload


@pytest.mark.parametrize("registry_type", [EventRegistry, CommandRegistry])
def test_registry_rejects_duplicates_and_version_gaps(registry_type: type) -> None:
    registry = registry_type()
    registry.register("Example", 1, ExampleV1)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("Example", 1, ExampleV1)
    with pytest.raises(ValueError, match="version gap"):
        registry.register("Example", 3, ExampleV2, upgrade_to_v2)


@pytest.mark.parametrize("registry_type", [EventRegistry, CommandRegistry])
def test_registry_accepts_an_explicit_greenfield_baseline_version(
    registry_type: type,
) -> None:
    registry = registry_type()

    registry.register("Example", 2, ExampleV2)

    assert registry.latest("Example").version == 2
    assert registry.upcast("Example", 2, {"value": 7, "label": "baseline"}) == (
        2,
        {"value": 7, "label": "baseline"},
    )


@pytest.mark.parametrize("registry_type", [EventRegistry, CommandRegistry])
def test_registry_requires_an_upcaster_for_each_new_version(
    registry_type: type,
) -> None:
    registry = registry_type()
    registry.register("Example", 1, ExampleV1)

    with pytest.raises(ValueError, match="upcaster"):
        registry.register("Example", 2, ExampleV2)


@pytest.mark.parametrize("registry_type", [EventRegistry, CommandRegistry])
def test_registry_resolves_latest_write_schema_and_purely_upcasts(
    registry_type: type,
) -> None:
    registry = registry_type()
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


def test_registry_rejects_unknown_type_or_version() -> None:
    registry = EventRegistry()
    registry.register("Example", 1, ExampleV1)

    with pytest.raises(KeyError, match="Unknown"):
        registry.latest("Unknown")
    with pytest.raises(KeyError, match="version 0"):
        registry.upcast("Example", 0, {})


@pytest.mark.parametrize("registry_type", [EventRegistry, CommandRegistry])
def test_registry_validates_and_normalizes_payload_already_at_latest_version(
    registry_type: type,
) -> None:
    registry = registry_type()
    registry.register("Example", 1, ExampleV1)

    version, payload = registry.upcast("Example", 1, {"value": "7"})

    assert version == 1
    assert payload == {"value": 7}
    with pytest.raises(ValidationError):
        registry.upcast("Example", 1, {"not_value": 7})
