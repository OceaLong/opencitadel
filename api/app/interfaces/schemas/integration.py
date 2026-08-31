"""Closed HTTP contracts for first-class MCP and A2A Integrations."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.domain.models.inference import ResourceVisibility
from app.domain.models.integration_runtime import MCPTransport
from app.domain.models.integration_server import A2AServerRecord, MCPServerRecord
from app.domain.models.tool_policy import ToolExecutionPolicy


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _MCPServerFields(_ClosedModel):
    name: str = Field(min_length=1, max_length=255)
    transport: MCPTransport = MCPTransport.STREAMABLE_HTTP
    enabled: bool = True
    description: str | None = None
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    transport_options: dict[str, JsonValue] = Field(default_factory=dict)
    tool_policies: dict[str, ToolExecutionPolicy] = Field(default_factory=dict)
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE

    @model_validator(mode="after")
    def validate_transport_fields(self) -> Self:
        if self.transport in {MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP} and not self.url:
            raise ValueError("HTTP MCP transport requires url")
        if self.transport is MCPTransport.STDIO and not self.command:
            raise ValueError("stdio MCP transport requires command")
        return self


class CreateMCPServerRequest(_MCPServerFields):
    pass


class UpdateMCPServerRequest(_MCPServerFields):
    pass


class CreateA2AServerRequest(_ClosedModel):
    base_url: str = Field(min_length=1)
    enabled: bool = True
    tool_policies: dict[str, ToolExecutionPolicy] = Field(default_factory=dict)
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE


class UpdateA2AServerRequest(CreateA2AServerRequest):
    pass


class SetIntegrationEnabledRequest(_ClosedModel):
    enabled: bool


class MCPServerResponse(_ClosedModel):
    id: str
    name: str
    transport: MCPTransport
    enabled: bool
    description: str | None
    command: str | None
    args: list[str] | None
    url: str | None
    headers: dict[str, str] | None
    env: dict[str, str] | None
    transport_options: dict[str, JsonValue]
    tool_policies: dict[str, ToolExecutionPolicy]
    owner_user_id: str | None
    team_id: str | None
    visibility: ResourceVisibility
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, record: MCPServerRecord) -> MCPServerResponse:
        return cls.model_validate(record.model_dump(mode="python"))


class MCPServerListResponse(_ClosedModel):
    items: list[MCPServerResponse]


class A2AServerResponse(_ClosedModel):
    id: str
    base_url: str
    enabled: bool
    tool_policies: dict[str, ToolExecutionPolicy]
    owner_user_id: str | None
    team_id: str | None
    visibility: ResourceVisibility
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, record: A2AServerRecord) -> A2AServerResponse:
        return cls.model_validate(record.model_dump(mode="python"))


class A2AServerListResponse(_ClosedModel):
    items: list[A2AServerResponse]


__all__ = [
    "A2AServerListResponse",
    "A2AServerResponse",
    "CreateA2AServerRequest",
    "CreateMCPServerRequest",
    "MCPServerListResponse",
    "MCPServerResponse",
    "SetIntegrationEnabledRequest",
    "UpdateA2AServerRequest",
    "UpdateMCPServerRequest",
]
