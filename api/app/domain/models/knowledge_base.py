#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.knowledge_citation import KnowledgeCitation


class KBSourceType(str, Enum):
    UPLOAD = "upload"
    ZIP = "zip"
    WEB = "web"
    CONFLUENCE = "confluence"
    FEISHU = "feishu"


class KBStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    CHUNKING = "chunking"
    INDEXING = "indexing"
    GRAPH_BUILDING = "graph_building"
    READY = "ready"
    FAILED = "failed"


class DocStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    READY = "ready"
    FAILED = "failed"


class ChunkLevel(str, Enum):
    PARENT = "parent"
    CHILD = "child"


class KnowledgeBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    status: KBStatus = KBStatus.PENDING
    doc_count: int = 0
    chunk_count: int = 0
    ingest_task_id: Optional[str] = None
    error: Optional[str] = None
    vector_degraded: bool = False
    legacy_v1_migrated: bool = False
    active_version_id: Optional[str] = None
    ready_doc_count: int = 0
    settings: Dict[str, Any] = Field(default_factory=dict)
    owner_user_id: Optional[str] = None
    team_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class KnowledgeDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    title: str
    source_type: KBSourceType = KBSourceType.UPLOAD
    source_ref: str = ""
    mime: str = ""
    file_id: Optional[str] = None
    page_count: int = 0
    status: DocStatus = DocStatus.PENDING
    error: Optional[str] = None
    warning: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

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
    version_id: Optional[str] = None
    parent_id: Optional[str] = None
    level: ChunkLevel = ChunkLevel.CHILD
    content: str = ""
    segmented_content: str = ""
    # PostgreSQL's already-normalized tsvector representation. This is set
    # only while cloning immutable imported chunks so the keyword index is
    # copied byte-for-byte instead of being tokenized again.
    content_tsv: Optional[str] = None
    embedding: List[float] = Field(default_factory=list)
    page_no: Optional[int] = None
    heading_path: str = ""
    ordinal: int = 0


class KnowledgeEntity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    version_id: Optional[str] = None
    name: str
    normalized_name: str = ""
    type: str = ""
    description: str = ""


class KnowledgeRelation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    version_id: Optional[str] = None
    src_entity_id: str
    dst_entity_id: str
    relation: str = ""
    chunk_id: Optional[str] = None


class KnowledgeEntityRef(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kb_id: str
    version_id: Optional[str] = None
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
    evidence: Tuple[KnowledgeCitation, ...] = ()


class KnowledgeGraphResponse(BaseModel):
    """Immutable projection of one exact published graph page."""

    model_config = ConfigDict(frozen=True)

    nodes: Tuple[KnowledgeGraphNode, ...] = ()
    edges: Tuple[KnowledgeGraphEdge, ...] = ()
    capability: bool
    next_cursor: Optional[str] = None


class DocTreeNode(BaseModel):
    name: str
    doc_id: str
    status: str = ""
    children: List["DocTreeNode"] = Field(default_factory=list)


DocTreeNode.model_rebuild()
