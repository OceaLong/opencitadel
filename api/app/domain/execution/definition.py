"""Code-owned execution semantics, independent of serialization schema versions."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.domain.execution.family import RunFamily
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.runtime_policy.context import ContextPolicy

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImplementationVersion = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}$")]


class DefinitionUnavailable(LookupError):
    """Operationally unavailable code: retain due work without poisoning the Run."""


class DefinitionIntegrityError(ValueError):
    """A published identity resolves to different code or an incomplete closure."""


class DefinitionValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SchemaCompatibility(DefinitionValue):
    name: ImplementationVersion
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)
    write_version: int = Field(ge=1)

    @model_validator(mode="after")
    def _range(self):
        if not self.minimum <= self.write_version <= self.maximum:
            raise ValueError("schema write version must lie within the supported read range")
        return self


class HandlerImplementation(DefinitionValue):
    activity_type: ImplementationVersion
    handler_version: ImplementationVersion


class DefinitionPin(DefinitionValue):
    family: RunFamily
    definition_version: int = Field(ge=1)
    manifest_digest: Digest
    planner_version: ImplementationVersion
    compiler_version: ImplementationVersion


class DefinitionManifest(DefinitionValue):
    format_version: Literal[1] = 1
    family: RunFamily
    definition_version: int = Field(ge=1)
    planner_version: ImplementationVersion
    compiler_version: ImplementationVersion
    context_protocol_version: int = Field(ge=1)
    handlers: tuple[HandlerImplementation, ...] = Field(min_length=1, max_length=128)
    command_schemas: tuple[SchemaCompatibility, ...] = Field(min_length=1, max_length=256)
    event_schemas: tuple[SchemaCompatibility, ...] = Field(min_length=1, max_length=256)
    summary_purposes: tuple[
        Literal["production", "evaluation_subject", "evaluation_judge"], ...
    ] = ()
    build_artifact_digest: Digest
    context_policy: ContextPolicy | None = None

    @model_serializer(mode="wrap")
    def _stored_shape(self, handler):
        value = handler(self)
        if self.context_policy is None:
            # Historical manifests have no attested context policy. Preserve
            # their raw digest; absence never acquires a runtime default.
            value.pop("context_policy", None)
        return value

    @model_validator(mode="after")
    def _canonical_closure(self):
        if (
            any(item.activity_type.startswith("context.") for item in self.handlers)
            and self.context_policy is None
        ):
            raise ValueError("context handlers require an attested immutable context policy")
        for field, key in (
            ("handlers", "activity_type"),
            ("command_schemas", "name"),
            ("event_schemas", "name"),
        ):
            values = getattr(self, field)
            names = [getattr(value, key) for value in values]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate {field} declaration")
            object.__setattr__(
                self, field, tuple(sorted(values, key=lambda value: getattr(value, key)))
            )
        if len(self.summary_purposes) != len(set(self.summary_purposes)):
            raise ValueError("duplicate summary purpose")
        object.__setattr__(self, "summary_purposes", tuple(sorted(self.summary_purposes)))
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()

    @property
    def pin(self) -> DefinitionPin:
        return DefinitionPin(
            family=self.family,
            definition_version=self.definition_version,
            manifest_digest=self.digest,
            planner_version=self.planner_version,
            compiler_version=self.compiler_version,
        )

    def handler_version(self, activity_type: str) -> str:
        for item in self.handlers:
            if item.activity_type == activity_type:
                return item.handler_version
        raise DefinitionIntegrityError(f"activity not declared by definition: {activity_type}")

    def require_pin(self, pin: DefinitionPin) -> None:
        if pin != self.pin:
            raise DefinitionIntegrityError("definition pin does not match its immutable manifest")


class WorkerCapabilities(DefinitionValue):
    worker_id: Annotated[str, Field(min_length=1, max_length=255)]
    boot_id: Annotated[str, Field(min_length=1, max_length=255)]
    build_artifact_digest: Digest
    definitions: tuple[DefinitionPin, ...] = Field(max_length=128)
    handlers: tuple[HandlerImplementation, ...] = Field(max_length=256)
    compilers: tuple[ImplementationVersion, ...] = Field(max_length=32)
    command_schemas: tuple[SchemaCompatibility, ...] = Field(max_length=256)
    event_schemas: tuple[SchemaCompatibility, ...] = Field(max_length=256)

    @model_validator(mode="after")
    def _unique(self):
        keys = [(pin.family, pin.definition_version) for pin in self.definitions]
        if len(keys) != len(set(keys)):
            raise ValueError("worker advertises conflicting definition identities")
        pairs = [(item.activity_type, item.handler_version) for item in self.handlers]
        if len(pairs) != len(set(pairs)) or len(self.compilers) != len(set(self.compilers)):
            raise ValueError("duplicate worker capability")
        for field in ("command_schemas", "event_schemas"):
            values = getattr(self, field)
            if len({item.name for item in values}) != len(values):
                raise ValueError("duplicate worker schema capability")
            object.__setattr__(self, field, tuple(sorted(values, key=lambda item: item.name)))
        object.__setattr__(
            self,
            "definitions",
            tuple(sorted(self.definitions, key=lambda pin: (pin.family, pin.definition_version))),
        )
        object.__setattr__(
            self,
            "handlers",
            tuple(
                sorted(self.handlers, key=lambda item: (item.activity_type, item.handler_version))
            ),
        )
        object.__setattr__(self, "compilers", tuple(sorted(self.compilers)))
        return self

    def supports(self, manifest: DefinitionManifest) -> bool:
        handlers = {(item.activity_type, item.handler_version) for item in self.handlers}

        def schemas_fit(required, available):
            by_name = {item.name: item for item in available}
            return all(
                item.name in by_name
                and by_name[item.name].minimum <= item.minimum
                and by_name[item.name].maximum >= item.maximum
                for item in required
            )

        return (
            manifest.pin in self.definitions
            and manifest.compiler_version in self.compilers
            and all(
                (item.activity_type, item.handler_version) in handlers for item in manifest.handlers
            )
            and schemas_fit(manifest.command_schemas, self.command_schemas)
            and schemas_fit(manifest.event_schemas, self.event_schemas)
        )
