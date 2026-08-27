from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.execution.run import RunStatus
from app.domain.models.codebase import SessionMode
from app.domain.models.knowledge_base import DocStatus, KBSourceType, KBStatus
from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.models.knowledge_version import KnowledgeVersionState


class CreateKnowledgeBaseRequest(BaseModel):
    name: str = "未命名知识库"
    settings: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    status: KBStatus
    doc_count: int = 0
    chunk_count: int = 0
    error: str | None = None
    vector_degraded: bool = False
    ready_doc_count: int = 0
    active_version_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ListKnowledgeBasesResponse(BaseModel):
    knowledge_bases: list[KnowledgeBaseResponse]


class KnowledgeDocumentResponse(BaseModel):
    id: str
    kb_id: str
    title: str
    source_type: KBSourceType
    mime: str = ""
    file_id: str | None = None
    page_count: int = 0
    status: DocStatus
    error: str | None = None
    warning: str | None = None
    created_at: datetime
    updated_at: datetime


class AddKnowledgeDocumentsRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    source_type: KBSourceType = KBSourceType.UPLOAD


class ListKnowledgeDocumentsResponse(BaseModel):
    documents: list[KnowledgeDocumentResponse]
    total: int = 0


class CreateKnowledgeBaseSessionRequest(BaseModel):
    mode: SessionMode = SessionMode.ASK
    model_id: str | None = None
    skill_id: str | None = None
    knowledge_base_version_id: str | None = None


class CreateKnowledgeBaseSessionResponse(BaseModel):
    session_id: str
    knowledge_base_id: str
    mode: SessionMode


class KnowledgeDocumentContentItemResponse(BaseModel):
    id: str
    page_no: int | None = None
    heading_path: str = ""
    ordinal: int = 0
    content: str = ""


class ReadKnowledgeDocumentResponse(BaseModel):
    document: KnowledgeDocumentResponse
    version_id: str
    document_revision_id: str
    items: list[KnowledgeDocumentContentItemResponse]
    next_cursor: str | None = None
    total: int
    truncated: bool


class KnowledgeGraphNodeResponse(BaseModel):
    id: str
    name: str
    type: str = ""
    description: str = ""


class KnowledgeGraphEdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    relation: str = ""
    evidence: list[KnowledgeCitation] = Field(default_factory=list)


class KnowledgeGraphResponse(BaseModel):
    nodes: list[KnowledgeGraphNodeResponse] = Field(default_factory=list)
    edges: list[KnowledgeGraphEdgeResponse] = Field(default_factory=list)
    capability: bool
    next_cursor: str | None = None


class KnowledgeBuildResponse(BaseModel):
    id: str
    run_id: str | None = None
    knowledge_base_id: str
    version_id: str
    status: RunStatus
    phase: str | None = None
    progress: int = 0
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None
    can_retry: bool = False
    can_cancel: bool = False


class KnowledgeVersionResponse(BaseModel):
    id: str
    knowledge_base_id: str
    parent_version_id: str | None = None
    build_id: str
    state: KnowledgeVersionState
    capabilities: dict[str, Any] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    published_at: datetime | None = None
    is_active: bool = False
    is_published: bool = False
    is_candidate: bool = False
    build: KnowledgeBuildResponse | None = None


class ListKnowledgeVersionsResponse(BaseModel):
    knowledge_base_id: str
    active_version_id: str | None = None
    active_build: KnowledgeBuildResponse | None = None
    versions: list[KnowledgeVersionResponse] = Field(default_factory=list)
