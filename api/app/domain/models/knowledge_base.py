import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.utils.time_utils import utc_now


class KBSourceType(StrEnum):
    UPLOAD = "upload"
    ZIP = "zip"
    WEB = "web"
    CONFLUENCE = "confluence"
    FEISHU = "feishu"


class KBStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    CHUNKING = "chunking"
    INDEXING = "indexing"
    GRAPH_BUILDING = "graph_building"
    READY = "ready"
    FAILED = "failed"


class DocStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    READY = "ready"
    FAILED = "failed"


class ChunkLevel(StrEnum):
    PARENT = "parent"
    CHILD = "child"


class KnowledgeBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    status: KBStatus = KBStatus.PENDING
    doc_count: int = 0
    chunk_count: int = 0
    error: str | None = None
    vector_degraded: bool = False
    active_version_id: str | None = None
    ready_doc_count: int = 0
    settings: dict[str, Any] = Field(default_factory=dict)
    owner_user_id: str | None = None
    team_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    title: str
    source_type: KBSourceType = KBSourceType.UPLOAD
    source_ref: str = ""
    mime: str = ""
    file_id: str | None = None
    page_count: int = 0
    status: DocStatus = DocStatus.PENDING
    error: str | None = None
    warning: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def source_identity(self) -> str:
        """Stable logical source identity; content identity lives on revisions."""
        if self.file_id:
            return f"file:{self.file_id}"
        return f"{self.source_type.value}:{self.source_ref.strip()}"


class KnowledgeChunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    doc_id: str
    version_id: str
    parent_id: str | None = None
    level: ChunkLevel = ChunkLevel.CHILD
    content: str = ""
    segmented_content: str = ""
    # Optional pre-normalized PostgreSQL tsvector representation.
    content_tsv: str | None = None
    embedding: list[float] = Field(default_factory=list)
    page_no: int | None = None
    heading_path: str = ""
    ordinal: int = 0


class KnowledgeEntity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    version_id: str
    name: str
    normalized_name: str = ""
    type: str = ""
    description: str = ""


class KnowledgeRelation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    version_id: str
    src_entity_id: str
    dst_entity_id: str
    relation: str = ""
    chunk_id: str | None = None


class KnowledgeEntityRef(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    version_id: str
    entity_id: str
    doc_id: str


class KnowledgeGraphNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    type: str = ""
    description: str = ""


class KnowledgeGraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    source: str
    target: str
    relation: str = ""
    evidence: tuple[KnowledgeCitation, ...] = ()


class KnowledgeGraphResponse(BaseModel):
    """Immutable projection of one exact published graph page."""

    model_config = ConfigDict(frozen=True)

    nodes: tuple[KnowledgeGraphNode, ...] = ()
    edges: tuple[KnowledgeGraphEdge, ...] = ()
    capability: bool
    next_cursor: str | None = None


class DocTreeNode(BaseModel):
    name: str
    doc_id: str
    status: str = ""
    children: list["DocTreeNode"] = Field(default_factory=list)


DocTreeNode.model_rebuild()
