"""Immutable identity and policy context for one admitted Run."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.execution.definition import DefinitionManifest, DefinitionPin
from app.domain.execution.family import RunFamily
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy.snapshot import (
    RunPolicySnapshot,
    validate_run_policy_snapshot,
)


class RunExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    family: RunFamily
    owner_scope: OwnerScope
    policy_snapshot: RunPolicySnapshot
    correlation_id: UUID
    definition_pin: DefinitionPin | None = None
    definition_manifest: DefinitionManifest | None = None
    # Product anchor of the Run (session, patrol_run, knowledge base build...).
    # Off-stream telemetry (activity progress) needs it to address the public
    # event feed without a projection lookup per report.
    source_entity_type: str | None = None
    source_entity_id: str | None = None

    @model_validator(mode="after")
    def validate_policy_family(self) -> RunExecutionContext:
        validate_run_policy_snapshot(self.policy_snapshot)
        if self.family != self.policy_snapshot.family:
            raise ValueError("Run context policy family mismatch")
        if self.definition_pin is not None and self.definition_pin.family != self.family:
            raise ValueError("Run context definition family mismatch")
        if self.definition_manifest is not None:
            if self.definition_pin is None:
                raise ValueError("Run context manifest requires a pin")
            self.definition_manifest.require_pin(self.definition_pin)
        return self


__all__ = ["RunExecutionContext"]
