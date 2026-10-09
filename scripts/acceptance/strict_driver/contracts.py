"""Typed non-secret identities shared by bootstrap and mounted producers."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResourceRef(StrictModel):
    id: UUID
    revision: StrictInt = Field(ge=1)


class BootstrapInput(StrictModel):
    schema_version: Literal[1]
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,47}$")
    project: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,47}$")
    operator_id: str = Field(min_length=1)
    # This producer uses a dedicated personal scope; no invented membership.
    scope: dict[str, str | None]
    session_id: str = Field(min_length=1)
    analysis_session_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    dataset: ResourceRef
    dataset_version: ResourceRef
    case_id: UUID
    configuration: ResourceRef
    configuration_version: ResourceRef
    suite: ResourceRef
    suite_version: ResourceRef
    environment: ResourceRef
    target: ResourceRef
    credentials: tuple[ResourceRef, ...]

    @model_validator(mode="after")
    def personal_scope(self):
        if self.scope != {"type": "personal", "user_id": self.operator_id, "team_id": None}:
            raise ValueError("strict bootstrap requires authenticated personal scope")
        return self


class Binding(StrictModel):
    schema_version: Literal[1]
    invocation_id: UUID
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,47}$")
    project: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,47}$")
    revision: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    dirty_tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    kernel_image: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    kernel_container: str = Field(pattern=r"^[0-9a-f]{64}$")
    migration: str = Field(min_length=1)
    inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DriverInput(StrictModel):
    binding: Binding
    bootstrap: BootstrapInput

    @model_validator(mode="after")
    def same_invocation(self):
        if (self.binding.run_id, self.binding.project) != (
            self.bootstrap.run_id,
            self.bootstrap.project,
        ):
            raise ValueError("foreign bootstrap")
        return self
