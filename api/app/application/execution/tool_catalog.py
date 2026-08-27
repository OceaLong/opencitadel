"""Fail-closed tool catalog contract used by conversational Activities."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.domain.execution.activity import ActivityContext
from app.domain.execution.commands import JsonValue


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    tool_schema: dict[str, JsonValue]
    requires_approval: bool
    risk_summary: str


class ExecutionToolCatalog(Protocol):
    async def definitions(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
    ) -> tuple[ToolDefinition, ...]: ...

    async def invoke(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
        *,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]: ...

    async def retrieve(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
        *,
        query: str,
    ) -> dict[str, JsonValue]: ...


__all__ = ["ExecutionToolCatalog", "ToolDefinition"]
