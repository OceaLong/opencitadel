"""Caller-facing tool invocation failures (tool contract v2, D8).

A ``ToolInvocationError`` is a *tool-level* failure: the model supplied bad
arguments, asked for a tool that is absent or denied, or the tool itself
rejected the request. The activity boundary normalizes it into a failed tool
result that is fed back to the model loop instead of failing the Run.
Infrastructure exceptions (connection, timeout, cancellation) must NOT use
this type — they keep their activity-failure semantics.
"""

from __future__ import annotations

from typing import Literal

ToolErrorKind = Literal[
    "invalid_arguments",
    "capability_denied",
    "not_found",
    "execution_failed",
]

_VALID_KINDS: frozenset[str] = frozenset(
    {
        "invalid_arguments",
        "capability_denied",
        "not_found",
        "execution_failed",
    }
)


class ToolInvocationError(Exception):
    """Tool-level failure that must flow back to the model, not kill the Run."""

    def __init__(self, message: str, *, kind: ToolErrorKind) -> None:
        if kind not in _VALID_KINDS:
            raise ValueError(f"unknown tool error kind: {kind}")
        super().__init__(message)
        self.kind: ToolErrorKind = kind


__all__ = ["ToolErrorKind", "ToolInvocationError"]
