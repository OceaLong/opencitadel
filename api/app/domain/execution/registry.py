"""Versioned command and event schema registries.

Evolution rules (see EVOLUTION.md, enforced by CI guards):

- A name's first registration is its baseline and may never be re-based once
  events of that type have been persisted anywhere.
- Any shape change requires a new version plus an upcaster from the previous
  version; the same version number must never change shape.
- Event upcasters transform *both* payload halves (public and internal), so
  internal structures such as the policy snapshot evolve through the same
  pipeline instead of silently bypassing it.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass

from pydantic import BaseModel

type Payload = dict[str, object]
type Upcaster = Callable[[Payload], Payload]
type EventUpcaster = Callable[["EventPayloads"], "EventPayloads"]


class UnregisteredSchemaError(ValueError):
    """A name or version has no registration.

    A ``ValueError`` subclass so read-side consumers (orchestrator, projector)
    treat it like any other validation failure instead of letting a bare
    ``KeyError`` escape their rollback handling.
    """


@dataclass(frozen=True)
class EventPayloads:
    """Both halves of one event's payload, upcast together."""

    public: Payload
    internal: Payload


@dataclass(frozen=True)
class CommandRegistration:
    name: str
    version: int
    model: type[BaseModel]
    upcast_from_previous: Upcaster | None


@dataclass(frozen=True)
class EventRegistration:
    name: str
    version: int
    model: type[BaseModel]
    internal_model: type[BaseModel] | None
    upcast_from_previous: EventUpcaster | None


def _check_version(
    name: str,
    version: int,
    versions: dict[int, object],
    has_upcaster: bool,
) -> None:
    if not name.strip():
        raise ValueError("schema name must not be empty")
    if version < 1:
        raise ValueError("schema version must be positive")
    if version in versions:
        raise ValueError(f"{name} version {version} is already registered")
    if versions:
        expected = max(versions) + 1
        if version != expected:
            raise ValueError(f"{name} version gap: expected {expected}, received {version}")
        if not has_upcaster:
            raise ValueError(f"{name} version {version} requires an upcaster")
    elif has_upcaster:
        raise ValueError(f"{name} baseline version cannot have an upcaster")


class CommandRegistry:
    """Registry for versioned command schemas and explicit upcasters."""

    def __init__(self) -> None:
        self._registrations: dict[str, dict[int, CommandRegistration]] = {}

    def register(
        self,
        name: str,
        version: int,
        model: type[BaseModel],
        upcast_from_previous: Upcaster | None = None,
    ) -> None:
        versions = self._registrations.setdefault(name, {})
        _check_version(name, version, versions, upcast_from_previous is not None)
        versions[version] = CommandRegistration(
            name=name,
            version=version,
            model=model,
            upcast_from_previous=upcast_from_previous,
        )

    def registered_names(self) -> frozenset[str]:
        return frozenset(self._registrations)

    def latest(self, name: str) -> CommandRegistration:
        versions = self._registrations.get(name)
        if not versions:
            raise UnregisteredSchemaError(f"Unknown schema type: {name}")
        return versions[max(versions)]

    def latest_version(self, name: str) -> int:
        return self.latest(name).version

    def upcast(
        self,
        name: str,
        from_version: int,
        payload: Payload,
    ) -> tuple[int, Payload]:
        versions = self._registrations.get(name)
        if not versions:
            raise UnregisteredSchemaError(f"Unknown schema type: {name}")
        if from_version not in versions:
            raise UnregisteredSchemaError(f"Unknown {name} version {from_version}")

        source_registration = versions[from_version]
        working = source_registration.model.model_validate(deepcopy(payload)).model_dump(
            mode="json"
        )
        latest_version = max(versions)
        for version in range(from_version + 1, latest_version + 1):
            registration = versions[version]
            upcaster = registration.upcast_from_previous
            if upcaster is None:  # guarded by register; retained as fail-closed.
                raise RuntimeError(f"Missing upcaster for {name} version {version}")
            working = upcaster(deepcopy(working))
            working = registration.model.model_validate(working).model_dump(mode="json")
        return latest_version, deepcopy(working)


class EventRegistry:
    """Registry for immutable event schemas and their read upcasters.

    Unlike commands, events carry two payload halves. Both are versioned by the
    single ``event_schema_version`` and upcast through one pipeline, so the
    internal payload (policy snapshots, input payloads, decision digests) has
    the same evolution channel as the public payload.
    """

    def __init__(self) -> None:
        self._registrations: dict[str, dict[int, EventRegistration]] = {}

    def register(
        self,
        name: str,
        version: int,
        model: type[BaseModel],
        *,
        internal_model: type[BaseModel] | None = None,
        upcast_from_previous: EventUpcaster | None = None,
    ) -> None:
        versions = self._registrations.setdefault(name, {})
        _check_version(name, version, versions, upcast_from_previous is not None)
        versions[version] = EventRegistration(
            name=name,
            version=version,
            model=model,
            internal_model=internal_model,
            upcast_from_previous=upcast_from_previous,
        )

    def registered_names(self) -> frozenset[str]:
        return frozenset(self._registrations)

    def latest(self, name: str) -> EventRegistration:
        versions = self._registrations.get(name)
        if not versions:
            raise UnregisteredSchemaError(f"Unknown schema type: {name}")
        return versions[max(versions)]

    def latest_version(self, name: str) -> int:
        return self.latest(name).version

    def _validated(
        self,
        registration: EventRegistration,
        payloads: EventPayloads,
    ) -> EventPayloads:
        public = registration.model.model_validate(deepcopy(payloads.public)).model_dump(
            mode="json"
        )
        internal = payloads.internal
        if registration.internal_model is not None:
            internal = registration.internal_model.model_validate(
                deepcopy(payloads.internal)
            ).model_dump(mode="json")
        return EventPayloads(public=public, internal=deepcopy(internal))

    def validate_new(self, name: str, version: int, payloads: EventPayloads) -> None:
        """Write-side guard: a newly emitted event must be the latest schema."""
        registration = self.latest(name)
        if version != registration.version:
            raise ValueError(
                f"{name} must be emitted at latest schema version "
                f"{registration.version}, received {version}"
            )
        self._validated(registration, payloads)

    def upcast(
        self,
        name: str,
        from_version: int,
        payloads: EventPayloads,
    ) -> tuple[int, EventPayloads]:
        versions = self._registrations.get(name)
        if not versions:
            raise UnregisteredSchemaError(f"Unknown schema type: {name}")
        if from_version not in versions:
            raise UnregisteredSchemaError(f"Unknown {name} version {from_version}")

        working = self._validated(versions[from_version], payloads)
        latest_version = max(versions)
        for version in range(from_version + 1, latest_version + 1):
            registration = versions[version]
            upcaster = registration.upcast_from_previous
            if upcaster is None:  # guarded by register; retained as fail-closed.
                raise RuntimeError(f"Missing upcaster for {name} version {version}")
            working = self._validated(registration, upcaster(working))
        return latest_version, working


__all__ = [
    "CommandRegistration",
    "CommandRegistry",
    "EventPayloads",
    "EventRegistration",
    "EventRegistry",
    "UnregisteredSchemaError",
]
