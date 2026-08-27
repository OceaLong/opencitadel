"""Immutable identity and policy context for one admitted Run."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

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

    @model_validator(mode="after")
    def validate_policy_family(self) -> RunExecutionContext:
        validate_run_policy_snapshot(self.policy_snapshot)
        if self.family != self.policy_snapshot.family:
            raise ValueError("Run context policy family mismatch")
        return self


__all__ = ["RunExecutionContext"]
