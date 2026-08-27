"""Versioned command and event schema registries."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass

from pydantic import BaseModel

type Payload = dict[str, object]
type Upcaster = Callable[[Payload], Payload]


@dataclass(frozen=True)
class SchemaRegistration:
    name: str
    version: int
    model: type[BaseModel]
    upcast_from_previous: Upcaster | None


class _VersionedRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, dict[int, SchemaRegistration]] = {}

    def register(
        self,
        name: str,
        version: int,
        model: type[BaseModel],
        upcast_from_previous: Upcaster | None = None,
    ) -> None:
        if not name.strip():
            raise ValueError("schema name must not be empty")
        if version < 1:
            raise ValueError("schema version must be positive")
        versions = self._registrations.setdefault(name, {})
        if version in versions:
            raise ValueError(f"{name} version {version} is already registered")
        if versions:
            expected = max(versions) + 1
            if version != expected:
                raise ValueError(f"{name} version gap: expected {expected}, received {version}")
            if upcast_from_previous is None:
                raise ValueError(f"{name} version {version} requires an upcaster")
        elif upcast_from_previous is not None:
            raise ValueError(f"{name} baseline version cannot have an upcaster")
        versions[version] = SchemaRegistration(
            name=name,
            version=version,
            model=model,
            upcast_from_previous=upcast_from_previous,
        )

    def latest(self, name: str) -> SchemaRegistration:
        versions = self._registrations.get(name)
        if not versions:
            raise KeyError(f"Unknown schema type: {name}")
        return versions[max(versions)]

    def upcast(
        self,
        name: str,
        from_version: int,
        payload: Payload,
    ) -> tuple[int, Payload]:
        versions = self._registrations.get(name)
        if not versions:
            raise KeyError(f"Unknown schema type: {name}")
        if from_version not in versions:
            raise KeyError(f"Unknown {name} version {from_version}")

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


class EventRegistry(_VersionedRegistry):
    """Registry for immutable event schemas and their read upcasters."""


class CommandRegistry(_VersionedRegistry):
    """Registry for versioned command schemas and explicit upcasters."""


__all__ = ["CommandRegistry", "EventRegistry", "SchemaRegistration"]
