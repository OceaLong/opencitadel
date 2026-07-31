#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Declarative execution metadata for agent tools."""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict

from pydantic import BaseModel


class ToolCapability(str, Enum):
    MESSAGE = "message"
    KNOWLEDGE_READ = "knowledge_read"
    CODE_READ = "code_read"
    INTEGRATION_READ = "integration_read"
    WEB_READ = "web_read"
    GENERATION = "generation"
    EXECUTION = "execution"
    UNKNOWN = "unknown"


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    EXTERNAL_WRITE = "external_write"
    INTERACTIVE = "interactive"


class ToolIdempotency(str, Enum):
    SAFE = "safe"
    IDEMPOTENT_WITH_KEY = "idempotent_with_key"
    NON_IDEMPOTENT = "non_idempotent"
    UNKNOWN = "unknown"


class ApprovalMode(str, Enum):
    NEVER = "never"
    POLICY = "policy"
    ALWAYS = "always"


class ToolExecutionPolicy(BaseModel):
    capability: ToolCapability
    effect: ToolEffect
    idempotency: ToolIdempotency
    approval: ApprovalMode
    concurrency_group: str = "none"


CONSERVATIVE_TOOL_POLICY = ToolExecutionPolicy(
    capability=ToolCapability.UNKNOWN,
    effect=ToolEffect.INTERACTIVE,
    idempotency=ToolIdempotency.UNKNOWN,
    approval=ApprovalMode.ALWAYS,
    concurrency_group="unknown",
)


@dataclass(frozen=True)
class ToolDescriptor:
    """The callable and metadata needed to govern a registered tool."""

    name: str
    schema: Dict[str, Any]
    method: Callable[..., Any]
    tool_pack: str
    policy: ToolExecutionPolicy
