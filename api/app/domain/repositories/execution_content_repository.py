"""Separately authorized immutable sanitized content, never private execution payloads."""

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from app.domain.models.scope import OwnerScope


class ExecutionContentRepository(Protocol):
    async def get_snapshot(
        self,
        scope: OwnerScope,
        content_id: str,
        run_id: UUID,
        step_id: str,
        formal_position: int,
        *,
        include_body: bool = True,
    ) -> Mapping[str, Any] | None: ...
    async def get_citation(self, scope: OwnerScope, citation_id: str) -> dict | None: ...
