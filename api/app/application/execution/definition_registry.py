"""Exact local bindings; database manifests never provide executable code."""

from dataclasses import dataclass
from typing import Any

from app.domain.execution.definition import (
    DefinitionIntegrityError,
    DefinitionManifest,
    DefinitionPin,
    DefinitionUnavailable,
)
from app.domain.execution.family import RunFamily


@dataclass(frozen=True)
class RegisteredDefinition:
    manifest: DefinitionManifest
    planner: Any
    compiler: Any


class DefinitionRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[RunFamily, int], RegisteredDefinition] = {}

    def register(self, manifest: DefinitionManifest, *, planner, compiler) -> None:
        key = (manifest.family, manifest.definition_version)
        previous = self._definitions.get(key)
        if previous is not None:
            if (
                previous.manifest != manifest
                or previous.planner is not planner
                or previous.compiler is not compiler
            ):
                raise DefinitionIntegrityError(
                    "definition identity is already bound to different code"
                )
            return
        if not callable(planner) or not callable(compiler):
            raise DefinitionIntegrityError(
                "definition requires executable local planner and compiler"
            )
        self._definitions[key] = RegisteredDefinition(manifest, planner, compiler)

    def resolve(self, pin: DefinitionPin) -> RegisteredDefinition:
        value = self._definitions.get((pin.family, pin.definition_version))
        if value is None:
            raise DefinitionUnavailable(
                f"execution definition unavailable: {pin.family.value}/{pin.definition_version}"
            )
        value.manifest.require_pin(pin)
        return value

    @property
    def manifests(self) -> tuple[DefinitionManifest, ...]:
        return tuple(
            value.manifest
            for _, value in sorted(
                self._definitions.items(), key=lambda item: (item[0][0].value, item[0][1])
            )
        )

    def validate_handlers(self, registry) -> None:
        for manifest in self.manifests:
            for binding in manifest.handlers:
                try:
                    registry.resolve(binding.activity_type, handler_version=binding.handler_version)
                except LookupError as error:
                    raise DefinitionIntegrityError(
                        "definition handler closure is incomplete"
                    ) from error
