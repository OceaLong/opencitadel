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
    # Declarative approval-card metadata (see ToolExecutionPolicy): which card
    # the UI renders, and which tool arguments supply the card's prompt text
    # and selectable choices. Data-driven — nothing matches on tool names.
    approval_kind: str = "tool_effect"
    approval_prompt_param: str | None = None
    approval_choices_param: str | None = None


class CatalogSnapshot(BaseModel):
    """One build's immutable view of the exposed tool catalog (D9).

    ``fingerprint`` digests the tool roster, per-tool policies, and the skill
    authorization so a later ``invoke`` can detect catalog drift between the
    model decision and tool execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    definitions: tuple[ToolDefinition, ...]
    fingerprint: str

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(definition.name for definition in self.definitions)


class ExecutionToolCatalog(Protocol):
    async def definitions(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
    ) -> CatalogSnapshot: ...

    async def invoke(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
        *,
        name: str,
        arguments: dict[str, JsonValue],
        expected_fingerprint: str | None = None,
        approval_feedback: str | None = None,
    ) -> dict[str, JsonValue]: ...

    async def retrieve(
        self,
        payload: dict[str, JsonValue],
        context: ActivityContext,
        *,
        query: str,
    ) -> dict[str, JsonValue]: ...


__all__ = ["CatalogSnapshot", "ExecutionToolCatalog", "ToolDefinition"]
