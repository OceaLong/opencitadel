#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Mode-scoped capability policy interfaces."""
from dataclasses import dataclass
from typing import Iterable, Optional

from app.domain.models.codebase import SessionMode
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
MESSAGE_READ = READ_SAFE.model_copy(update={"capability": ToolCapability.MESSAGE})
CODE_READ = READ_SAFE.model_copy(update={"capability": ToolCapability.CODE_READ})
INTEGRATION_READ = READ_SAFE.model_copy(update={"capability": ToolCapability.INTEGRATION_READ})
WEB_READ = READ_SAFE.model_copy(update={"capability": ToolCapability.WEB_READ})
SHELL_INTERACTIVE = INTERACTIVE_BROWSER.model_copy(
    update={"concurrency_group": "shell"}
)
GENERATION_WRITE = ToolExecutionPolicy(
    capability=ToolCapability.GENERATION,
    effect=ToolEffect.EXTERNAL_WRITE,
    idempotency=ToolIdempotency.UNKNOWN,
    approval=ApprovalMode.POLICY,
    concurrency_group="generation",
)

_ASK_CAPABILITIES = frozenset({
    ToolCapability.MESSAGE,
    ToolCapability.KNOWLEDGE_READ,
    ToolCapability.CODE_READ,
    ToolCapability.INTEGRATION_READ,
})


class CapabilityDeniedError(PermissionError):
    """Raised when a tool request exceeds the active capability policy.

    ``layer`` identifies which enforcement layer produced the denial, for
    governance observability (Phase A / Task 2):
      - ``assembly``: denied while assembling a (sub-)agent's capability
        policy, before any tool set was exposed (e.g. an Ask sub-agent with
        no explicit allowlist requesting tools at all).
      - ``exposure``: denied because the request would expose a tool beyond
        what the parent policy already granted.
      - ``execution`` (default): denied at the point of actually invoking a
        tool whose descriptor policy the active ``CapabilityPolicy`` rejects.
    ``tool_name`` carries the concrete tool name being denied when the
    raise site has one in hand; ``None`` when the denial concerns a set of
    requested tools rather than a single call (e.g. ``for_child``'s
    no-allowlist-at-all case).
    """

    def __init__(
            self,
            message: str,
            *,
            layer: str = "execution",
            tool_name: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.layer = layer
        self.tool_name = tool_name


@dataclass(frozen=True)
class CapabilityPolicy:
    mode: SessionMode
    allowed_tool_names: Optional[frozenset[str]] = None

    @classmethod
    def for_mode(
            cls,
            mode: SessionMode,
            allowed_tool_names: Optional[Iterable[str]] = None,
    ) -> "CapabilityPolicy":
        names = frozenset(allowed_tool_names) if allowed_tool_names is not None else None
        return cls(mode=mode, allowed_tool_names=names)

    def allows(
            self,
            execution_policy: ToolExecutionPolicy,
            *,
            tool_name: Optional[str] = None,
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
            tool_name: Optional[str] = None,
    ) -> bool:
        """Require integration declarations to use their dedicated read capability."""
        if self.mode == SessionMode.ASK and (
            execution_policy.capability != ToolCapability.INTEGRATION_READ
        ):
            return False
        return self.allows(execution_policy, tool_name=tool_name)

    def for_child(self, requested_tool_names: Optional[Iterable[str]] = None) -> "CapabilityPolicy":
        if requested_tool_names is None:
            return self
        requested = frozenset(requested_tool_names)
        if self.mode == SessionMode.ASK and self.allowed_tool_names is None and requested:
            raise CapabilityDeniedError(
                "Ask 子 Agent 不得请求未由父策略显式授予的工具",
                layer="assembly",
            )
        if self.allowed_tool_names is not None:
            expanded = [
                name for name in requested
                if not is_tool_allowed(name, list(self.allowed_tool_names))
            ]
            if expanded:
                raise CapabilityDeniedError(
                    f"子 Agent 工具请求超出父策略: {', '.join(sorted(expanded))}",
                    layer="exposure",
                    tool_name=", ".join(sorted(expanded)),
                )
        return CapabilityPolicy(mode=self.mode, allowed_tool_names=requested)
