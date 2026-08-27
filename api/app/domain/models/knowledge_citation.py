"""Immutable provenance for knowledge-base retrieval results."""

from pydantic import BaseModel, ConfigDict, field_validator


class KnowledgeCitation(BaseModel):
    """Exact immutable manifest identity behind one presented KB chunk."""

    model_config = ConfigDict(frozen=True)

    version_id: str
    document_revision_id: str
    doc_id: str
    page_no: int | None = None
    chunk_id: str

    @field_validator(
        "version_id",
        "document_revision_id",
        "doc_id",
        "chunk_id",
    )
    @classmethod
    def _require_identity(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("knowledge citation identity cannot be empty")
        return normalized


def deduplicate_citations(
    citations: list[KnowledgeCitation] | tuple[KnowledgeCitation, ...],
) -> list[KnowledgeCitation]:
    """Return stable first-seen citation order with exact identity deduping."""
    out: list[KnowledgeCitation] = []
    seen: set[tuple[str, str, str, int | None, str]] = set()
    for citation in citations:
        key = (
            citation.version_id,
            citation.document_revision_id,
            citation.doc_id,
            citation.page_no,
            citation.chunk_id,
        )
        if key not in seen:
            seen.add(key)
            out.append(citation)
    return out
