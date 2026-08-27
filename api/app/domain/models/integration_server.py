from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from app.domain.models.inference import ResourceVisibility
from app.domain.models.integration_runtime import (
    MCPTransport,
    normalize_integration_tool_policies,
)
from app.domain.models.tool_policy import ToolExecutionPolicy
from app.domain.utils.secret_masking import mask_url
from app.domain.utils.time_utils import utc_now


class MCPServerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
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
    owner_user_id: str | None = None
    team_id: str | None = None
    visibility: ResourceVisibility = ResourceVisibility.GLOBAL
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("tool_policies")
    @classmethod
    def validate_tool_policies(cls, value):
        return normalize_integration_tool_policies(value)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> "MCPServerRecord":
        if self.transport in (MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP):
            if not self.url:
                raise ValueError("sse/streamable_http 模式必须提供 url")
            parsed = urlparse(self.url.strip())
            if parsed.scheme not in ("http", "https"):
                raise ValueError("MCP URL 必须以 http:// 或 https:// 开头")
        if self.transport == MCPTransport.STDIO and not self.command:
            raise ValueError("stdio 模式必须提供 command")
        return self

    def mask_secrets(self) -> "MCPServerRecord":
        masked = self.model_copy(deep=True)
        if masked.url:
            masked.url = mask_url(masked.url)
        if masked.headers:
            masked.headers = {k: _mask_value(v) for k, v in masked.headers.items()}
        if masked.env:
            masked.env = {k: _mask_value(v) for k, v in masked.env.items()}
        return masked


class A2AServerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    base_url: str
    enabled: bool = True
    tool_policies: dict[str, ToolExecutionPolicy] = Field(default_factory=dict)
    owner_user_id: str | None = None
    team_id: str | None = None
    visibility: ResourceVisibility = ResourceVisibility.GLOBAL
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("tool_policies")
    @classmethod
    def validate_tool_policies(cls, value):
        return normalize_integration_tool_policies(value)


def _mask_value(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]
