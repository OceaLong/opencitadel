"""Infrastructure adapters for externally hosted knowledge sources."""

from app.infrastructure.external.knowledge.web_connector import (
    HttpWebDocumentGateway,
)

__all__ = ["HttpWebDocumentGateway"]
