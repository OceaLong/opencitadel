"""Closed immutable runtime contracts for persisted Integration resources."""

from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from app.domain.models.tool_policy import (
    CONSERVATIVE_TOOL_POLICY,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
)


def normalize_integration_tool_policies(
    policies: dict[str, ToolExecutionPolicy],
) -> dict[str, ToolExecutionPolicy]:
    """Fail closed for read declarations using a non-integration capability."""
    return {
        name: (
            CONSERVATIVE_TOOL_POLICY
            if policy.effect == ToolEffect.READ_ONLY
            and policy.capability != ToolCapability.INTEGRATION_READ
            else policy
        )
        for name, policy in policies.items()
    }


class MCPTransport(StrEnum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    transport: MCPTransport = MCPTransport.STREAMABLE_HTTP
    enabled: bool = True
    description: str | None = None
    command: str | None = None
    args: tuple[str, ...] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    transport_options: dict[str, JsonValue] = Field(default_factory=dict)
    tool_policies: dict[str, ToolExecutionPolicy] = Field(default_factory=dict)

    @field_validator("id", "name")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("integration identity must not be blank")
        return normalized

    @field_validator("tool_policies")
    @classmethod
    def validate_tool_policies(
        cls,
        value: dict[str, ToolExecutionPolicy],
    ) -> dict[str, ToolExecutionPolicy]:
        return normalize_integration_tool_policies(value)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> MCPServerRuntime:
        if self.transport in {MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP}:
            if not self.url:
                raise ValueError("HTTP MCP transport requires url")
            parsed = urlparse(self.url.strip())
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("MCP URL must use http or https")
        if self.transport is MCPTransport.STDIO and not self.command:
            raise ValueError("stdio MCP transport requires command")
        return self


class MCPRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    servers: dict[str, MCPServerRuntime] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_stable_id_keys(self) -> MCPRuntime:
        names: set[str] = set()
        for server_id, server in self.servers.items():
            if server_id != server.id:
                raise ValueError("MCP runtime server key must equal server.id")
            if server.name in names:
                raise ValueError("MCP runtime server names must be unique")
            names.add(server.name)
        return self


class A2AServerRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=255)
    base_url: str = Field(min_length=1)
    enabled: bool = True
    tool_policies: dict[str, ToolExecutionPolicy] = Field(default_factory=dict)

    @field_validator("id", "base_url")
    @classmethod
    def normalize_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("integration value must not be blank")
        return normalized

    @field_validator("tool_policies")
    @classmethod
    def validate_tool_policies(
        cls,
        value: dict[str, ToolExecutionPolicy],
    ) -> dict[str, ToolExecutionPolicy]:
        return normalize_integration_tool_policies(value)

    @model_validator(mode="after")
    def validate_base_url(self) -> A2AServerRuntime:
        if urlparse(self.base_url).scheme not in {"http", "https"}:
            raise ValueError("A2A base URL must use http or https")
        return self


class A2ARuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    servers: tuple[A2AServerRuntime, ...] = ()

    @model_validator(mode="after")
    def require_unique_ids(self) -> A2ARuntime:
        ids = [server.id for server in self.servers]
        if len(ids) != len(set(ids)):
            raise ValueError("A2A runtime server ids must be unique")
        return self


__all__ = [
    "A2ARuntime",
    "A2AServerRuntime",
    "MCPRuntime",
    "MCPServerRuntime",
    "MCPTransport",
    "normalize_integration_tool_policies",
]
