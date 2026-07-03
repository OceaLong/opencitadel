#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, model_validator

from app.domain.models.app_config import MCPTransport
from app.domain.models.llm_model import ResourceVisibility


class MCPServerRecord(BaseModel):
    id: str
    name: str
    transport: MCPTransport = MCPTransport.STREAMABLE_HTTP
    enabled: bool = True
    description: Optional[str] = None
    command: Optional[str] = None
    args: Optional[list] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, Any]] = None
    env: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
    owner_user_id: Optional[str] = None
    visibility: ResourceVisibility = ResourceVisibility.GLOBAL
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> "MCPServerRecord":
        if self.transport in (MCPTransport.SSE, MCPTransport.STREAMABLE_HTTP) and not self.url:
            raise ValueError("sse/streamable_http 模式必须提供 url")
        if self.transport == MCPTransport.STDIO and not self.command:
            raise ValueError("stdio 模式必须提供 command")
        return self

    def mask_secrets(self) -> "MCPServerRecord":
        masked = self.model_copy(deep=True)
        if masked.headers:
            masked.headers = {k: _mask_value(v) for k, v in masked.headers.items()}
        if masked.env:
            masked.env = {k: _mask_value(v) for k, v in masked.env.items()}
        return masked


class A2AServerRecord(BaseModel):
    id: str
    base_url: str
    enabled: bool = True
    owner_user_id: Optional[str] = None
    visibility: ResourceVisibility = ResourceVisibility.GLOBAL
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


def _mask_value(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]
