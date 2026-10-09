"""Closed E04 broker protocol; no Docker argv, programs, targets or credentials."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.evaluation.environment import (
    EnvironmentLease,
    EnvironmentOperation,
    EnvironmentVersion,
)


class LeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease: EnvironmentLease


class LifecycleRequest(LeaseRequest):
    operation: EnvironmentOperation
    version: EnvironmentVersion

    @model_validator(mode="after")
    def identity(self):
        if (
            self.operation.lease_id != self.lease.id
            or self.operation.generation != self.lease.generation
            or self.operation.lease_revision != self.lease.revision
            or self.version.id != self.lease.environment_version
        ):
            raise ValueError("environment_operation_identity_mismatch")
        return self


class ControlRequest(LeaseRequest):
    path: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "POST"]
    body: str = Field(default="", max_length=28_000_000)
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)


class BrowserRequest(LeaseRequest):
    url: str = Field(min_length=1, max_length=4096)


class TargetRequest(LeaseRequest):
    target_id: str = Field(min_length=1, max_length=100)
    body: str = Field(max_length=1_500_000)
