"""Mode-scoped capability policy interfaces."""

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.models.session_mode import SessionMode
from app.domain.models.tool_policy import (
    ApprovalMode,
    ToolCapability,
    ToolEffect,
    ToolExecutionPolicy,
    ToolIdempotency,
)
from app.domain.services.tools.tool_names import is_tool_allowed

READ_SAFE = ToolExecutionPolicy(
    capability=ToolCapability.KNOWLEDGE_READ,
    effect=ToolEffect.READ_ONLY,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.NEVER,
)
WORKSPACE_WRITE = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.WORKSPACE_WRITE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.POLICY,
    concurrency_group="filesystem",
)
EXTERNAL_WRITE = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.EXTERNAL_WRITE,
    idempotency=ToolIdempotency.UNKNOWN,
    approval=ApprovalMode.ALWAYS,
    concurrency_group="integration",
)
INTERACTIVE_BROWSER = ToolExecutionPolicy(
    capability=ToolCapability.EXECUTION,
    effect=ToolEffect.INTERACTIVE,
    idempotency=ToolIdempotency.NON_IDEMPOTENT,
    approval=ApprovalMode.POLICY,
    concurrency_group="browser",
)
CODE_READ = READ_SAFE.model_copy(update={"capability": ToolCapability.CODE_READ})
INTEGRATION_READ = READ_SAFE.model_copy(update={"capability": ToolCapability.INTEGRATION_READ})
WEB_READ = READ_SAFE.model_copy(update={"capability": ToolCapability.WEB_READ})
SHELL_INTERACTIVE = INTERACTIVE_BROWSER.model_copy(update={"concurrency_group": "shell"})
GENERATION_WRITE = ToolExecutionPolicy(
    capability=ToolCapability.GENERATION,
    effect=ToolEffect.EXTERNAL_WRITE,
    idempotency=ToolIdempotency.UNKNOWN,
    approval=ApprovalMode.POLICY,
    concurrency_group="generation",
)
# ask_user: the model pauses the Run with a clarifying question and recommended
# options; the human's choice IS the approval feedback, injected back into the
# tool call on resume. approval=ALWAYS routes it through the standard waiting
# gate — no name-based special case exists anywhere in the loops.
CLARIFICATION_INTERACTIVE = ToolExecutionPolicy(
    capability=ToolCapability.UNKNOWN,
    effect=ToolEffect.INTERACTIVE,
    idempotency=ToolIdempotency.SAFE,
    approval=ApprovalMode.ALWAYS,
    concurrency_group="none",
    approval_kind="clarification",
    approval_prompt_param="question",
    approval_choices_param="options",
    approval_feedback_param="resolved_choice",
)

_ASK_CAPABILITIES = frozenset(
    {
        ToolCapability.KNOWLEDGE_READ,
        ToolCapability.CODE_READ,
        ToolCapability.INTEGRATION_READ,
    }
)


class CapabilityDeniedError(PermissionError):
    """Raised when assembly, exposure, or execution exceeds policy."""

    def __init__(
        self,
        message: str,
        *,
        layer: str = "execution",
        tool_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.layer = layer
        self.tool_name = tool_name


@dataclass(frozen=True)
class CapabilityPolicy:
    mode: SessionMode
    allowed_tool_names: frozenset[str] | None = None

    @classmethod
    def for_mode(
        cls,
        mode: SessionMode,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> "CapabilityPolicy":
        names = frozenset(allowed_tool_names) if allowed_tool_names is not None else None
        return cls(mode=mode, allowed_tool_names=names)

    def allows(
        self,
        execution_policy: ToolExecutionPolicy,
        *,
        tool_name: str | None = None,
    ) -> bool:
        if (
            tool_name is not None
            and self.allowed_tool_names is not None
            and not is_tool_allowed(tool_name, list(self.allowed_tool_names))
        ):
            return False
        if self.mode == SessionMode.AGENT:
            return True
        return (
            execution_policy.effect == ToolEffect.READ_ONLY
            and execution_policy.capability in _ASK_CAPABILITIES
        )

    def allows_integration(
        self,
        execution_policy: ToolExecutionPolicy,
        *,
        tool_name: str | None = None,
    ) -> bool:
        """Require integration declarations to use their dedicated read capability."""
        if self.mode == SessionMode.ASK and (
            execution_policy.capability != ToolCapability.INTEGRATION_READ
        ):
            return False
        return self.allows(execution_policy, tool_name=tool_name)
