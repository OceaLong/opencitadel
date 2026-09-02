"""Inference-owned transactional operations."""

from __future__ import annotations

from typing import Any, Protocol


class InferenceTransaction(Protocol):
    async def resolve_model(self, binding_id: str) -> dict[str, Any] | None: ...

    async def record_usage(self, usage: dict[str, Any]) -> None: ...
