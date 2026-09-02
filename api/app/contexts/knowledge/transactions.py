"""Knowledge-owned transactional operations."""

from __future__ import annotations

from typing import Any, Protocol


class KnowledgeTransaction(Protocol):
    async def get_published_version(self, version_id: str) -> dict[str, Any] | None: ...

    async def publish_candidate(self, candidate: dict[str, Any]) -> None: ...
