"""Domain port for externally hosted knowledge documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.models.knowledge_base import KBSourceType


@dataclass(frozen=True)
class WebDocument:
    title: str
    content: str
    mime: str = "text/markdown"


class WebDocumentGateway(Protocol):
    async def fetch(self, source_type: KBSourceType, url: str) -> WebDocument:
        """Fetch and normalize one externally hosted document."""
        ...


__all__ = ["WebDocument", "WebDocumentGateway"]
