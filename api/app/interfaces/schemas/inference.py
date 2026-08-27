from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.inference import (
    ChatModelSettings,
    EmbeddingModelSettings,
    InferenceBinding,
    InferenceCapabilities,
    InferenceEndpoint,
    InferenceModel,
    InferenceModelKind,
    InferenceProbeResult,
    InferenceProbeStatus,
    InferenceProvider,
    InferencePurpose,
    ResourceVisibility,
)


class _ClosedSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InferenceEndpointUpsertRequest(_ClosedSchema):
    display_name: str
    provider: InferenceProvider
    base_url: str
    credential: str = ""
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE


class InferenceEndpointResponse(_ClosedSchema):
    id: str
    display_name: str
    provider: InferenceProvider
    base_url: str
    credential_configured: bool
    visibility: ResourceVisibility
    owner_user_id: str | None = None
    team_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, endpoint: InferenceEndpoint) -> InferenceEndpointResponse:
        data = endpoint.model_dump(exclude={"credential"})
        return cls.model_validate(data)


class InferenceEndpointListResponse(_ClosedSchema):
    items: list[InferenceEndpointResponse] = Field(default_factory=list)


class InferenceModelUpsertRequest(_ClosedSchema):
    endpoint_id: str
    display_name: str
    model_name: str
    kind: InferenceModelKind
    settings: ChatModelSettings | EmbeddingModelSettings
    input_price_per_million: float = Field(default=0, ge=0)
    output_price_per_million: float = Field(default=0, ge=0)
    extra_params: dict[str, Any] = Field(default_factory=dict)
    capabilities: InferenceCapabilities = Field(default_factory=InferenceCapabilities)
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE


class InferenceModelResponse(_ClosedSchema):
    id: str
    endpoint_id: str
    display_name: str
    model_name: str
    kind: InferenceModelKind
    settings: ChatModelSettings | EmbeddingModelSettings
    input_price_per_million: float
    output_price_per_million: float
    extra_params: dict[str, Any]
    capabilities: InferenceCapabilities
    visibility: ResourceVisibility
    owner_user_id: str | None = None
    team_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, model: InferenceModel) -> InferenceModelResponse:
        return cls.model_validate(model.model_dump())


class InferenceModelListResponse(_ClosedSchema):
    items: list[InferenceModelResponse] = Field(default_factory=list)


class InferenceProbeResponse(_ClosedSchema):
    status: InferenceProbeStatus
    message: str
    error_key: str | None = None

    @classmethod
    def from_domain(cls, result: InferenceProbeResult) -> InferenceProbeResponse:
        return cls.model_validate(result.model_dump())


class InferenceBindingScope(StrEnum):
    WORKSPACE = "workspace"
    GLOBAL = "global"


class InferenceBindingRequest(_ClosedSchema):
    model_id: str
    binding_scope: InferenceBindingScope = InferenceBindingScope.WORKSPACE


class InferenceBindingResponse(_ClosedSchema):
    id: str
    purpose: InferencePurpose
    model_id: str
    owner_user_id: str | None = None
    team_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, binding: InferenceBinding) -> InferenceBindingResponse:
        return cls.model_validate(binding.model_dump())


class InferenceBindingListResponse(_ClosedSchema):
    items: list[InferenceBindingResponse] = Field(default_factory=list)


class InferenceStatusResponse(_ClosedSchema):
    capabilities: dict[str, Any]
    circuit_breakers: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
