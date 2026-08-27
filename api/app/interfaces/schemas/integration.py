"""Closed HTTP contracts for first-class MCP and A2A Integrations."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.application.services.integration_projection_service import (
    A2AServerProjection,
    IntegrationConnectionStatus,
    MCPServerProjection,
)
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


class MCPToolResponse(_ClosedModel):
    name: str
    description: str | None = None
    input_schema: dict[str, JsonValue]


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
    tools: list[MCPToolResponse] = Field(default_factory=list)
    connection_status: IntegrationConnectionStatus
    connection_error: str | None = None

    @classmethod
    def from_domain(cls, record: MCPServerRecord) -> MCPServerResponse:
        return cls.model_validate(
            {
                **record.model_dump(mode="python"),
                "connection_status": (
                    IntegrationConnectionStatus.CHECKING
                    if record.enabled
                    else IntegrationConnectionStatus.DISABLED
                ),
            }
        )

    @classmethod
    def from_projection(cls, projection: MCPServerProjection) -> MCPServerResponse:
        return cls.model_validate(
            {
                **projection.record.model_dump(mode="python"),
                "tools": [tool.model_dump(mode="python") for tool in projection.tools],
                "connection_status": projection.connection_status,
                "connection_error": projection.connection_error,
            }
        )


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
    agent_card: dict[str, JsonValue] | None = None
    connection_status: IntegrationConnectionStatus
    connection_error: str | None = None

    @classmethod
    def from_domain(cls, record: A2AServerRecord) -> A2AServerResponse:
        return cls.model_validate(
            {
                **record.model_dump(mode="python"),
                "connection_status": (
                    IntegrationConnectionStatus.CHECKING
                    if record.enabled
                    else IntegrationConnectionStatus.DISABLED
                ),
            }
        )

    @classmethod
    def from_projection(cls, projection: A2AServerProjection) -> A2AServerResponse:
        return cls.model_validate(
            {
                **projection.record.model_dump(mode="python"),
                "agent_card": projection.agent_card,
                "connection_status": projection.connection_status,
                "connection_error": projection.connection_error,
            }
        )


class A2AServerListResponse(_ClosedModel):
    items: list[A2AServerResponse]


__all__ = [
    "A2AServerListResponse",
    "A2AServerResponse",
    "CreateA2AServerRequest",
    "CreateMCPServerRequest",
    "MCPServerListResponse",
    "MCPServerResponse",
    "MCPToolResponse",
    "SetIntegrationEnabledRequest",
    "UpdateA2AServerRequest",
    "UpdateMCPServerRequest",
]
