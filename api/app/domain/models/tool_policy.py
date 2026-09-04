"""Declarative execution metadata for agent tools."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ToolCapability(StrEnum):
    KNOWLEDGE_READ = "knowledge_read"
    CODE_READ = "code_read"
    INTEGRATION_READ = "integration_read"
    WEB_READ = "web_read"
    GENERATION = "generation"
    EXECUTION = "execution"
    UNKNOWN = "unknown"


class ToolEffect(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    EXTERNAL_WRITE = "external_write"
    INTERACTIVE = "interactive"


class ToolIdempotency(StrEnum):
    SAFE = "safe"
    IDEMPOTENT_WITH_KEY = "idempotent_with_key"
    NON_IDEMPOTENT = "non_idempotent"
    UNKNOWN = "unknown"


class ApprovalMode(StrEnum):
    NEVER = "never"
    POLICY = "policy"
    ALWAYS = "always"


class ToolExecutionPolicy(BaseModel):
    capability: ToolCapability
    effect: ToolEffect
    idempotency: ToolIdempotency
    approval: ApprovalMode
    concurrency_group: str = "none"
    # Interaction metadata for the approval gate, declared by the tool instead
    # of name-based special cases anywhere in the core loops:
    # - approval_kind labels the waiting card the UI renders ("tool_effect" is
    #   the classic approve/reject gate; "clarification" renders the question +
    #   recommended-options card).
    # - approval_prompt_param names the tool argument whose value becomes the
    #   card's prompt text (falls back to the derived risk summary).
    # - approval_choices_param names the argument supplying selectable options.
    # - approval_feedback_param names the argument the reviewer's feedback (the
    #   chosen option) is injected into when the approved call executes.
    approval_kind: str = "tool_effect"
    approval_prompt_param: str | None = None
    approval_choices_param: str | None = None
    approval_feedback_param: str | None = None

    def requires_approval(self) -> bool:
        """Whether an execution under this policy needs a human approval gate.

        单源推导（D10/P2-11）：任何显式审批模式之外，非只读副作用也一律
        需要审批——曾经散落在目录装配处的硬编码规则收敛到这里。
        """
        return self.approval != ApprovalMode.NEVER or self.effect != ToolEffect.READ_ONLY


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
    schema: dict[str, Any]
    method: Callable[..., Any]
    tool_pack: str
    policy: ToolExecutionPolicy
