#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.models.codebase import SessionMode
from app.domain.models.knowledge_base import DocStatus, KBSourceType, KBStatus
from app.domain.models.knowledge_version import KnowledgeVersionState
from app.domain.models.resource_governance import BuildState
from app.domain.models.knowledge_citation import KnowledgeCitation


class CreateKnowledgeBaseRequest(BaseModel):
    name: str = "未命名知识库"
    settings: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    status: KBStatus
    doc_count: int = 0
    chunk_count: int = 0
    ingest_task_id: Optional[str] = None
    error: Optional[str] = None
    vector_degraded: bool = False
    ready_doc_count: int = 0
    active_version_id: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ListKnowledgeBasesResponse(BaseModel):
    knowledge_bases: List[KnowledgeBaseResponse]


class KnowledgeDocumentResponse(BaseModel):
    id: str
    kb_id: str
    title: str
    source_type: KBSourceType
    mime: str = ""
    file_id: Optional[str] = None
    page_count: int = 0
    status: DocStatus
    error: Optional[str] = None
    warning: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AddKnowledgeDocumentsRequest(BaseModel):
    file_ids: List[str] = Field(default_factory=list)
    urls: List[str] = Field(default_factory=list)
    source_type: KBSourceType = KBSourceType.UPLOAD


class ListKnowledgeDocumentsResponse(BaseModel):
    documents: List[KnowledgeDocumentResponse]
    total: int = 0


class CreateKnowledgeBaseSessionRequest(BaseModel):
    mode: SessionMode = SessionMode.ASK
    model_id: Optional[str] = None
    skill_id: Optional[str] = None
    knowledge_base_version_id: Optional[str] = None


class CreateKnowledgeBaseSessionResponse(BaseModel):
    session_id: str
    knowledge_base_id: str
    mode: SessionMode


class KnowledgeDocumentContentItemResponse(BaseModel):
    id: str
    page_no: Optional[int] = None
    heading_path: str = ""
    ordinal: int = 0
    content: str = ""


class ReadKnowledgeDocumentResponse(BaseModel):
    document: KnowledgeDocumentResponse
    content: str
    version_id: Optional[str] = None
    document_revision_id: Optional[str] = None
    items: List[KnowledgeDocumentContentItemResponse] = Field(
        default_factory=list
    )
    next_cursor: Optional[str] = None
    total: int = 0
    truncated: bool = False


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
    evidence: List[KnowledgeCitation] = Field(default_factory=list)


class KnowledgeGraphResponse(BaseModel):
    nodes: List[KnowledgeGraphNodeResponse] = Field(default_factory=list)
    edges: List[KnowledgeGraphEdgeResponse] = Field(default_factory=list)
    capability: bool
    next_cursor: Optional[str] = None


class KnowledgeBuildResponse(BaseModel):
    id: str
    knowledge_base_id: str
    version_id: str
    parent_version_id: Optional[str] = None
    command_key: str
    state: BuildState
    phase: Optional[str] = None
    progress: float = 0.0
    capabilities: List[Any] = Field(default_factory=list)
    degraded_reasons: List[Any] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    heartbeat_at: Optional[datetime] = None
    last_event_seq: int = 0
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    can_retry: bool = False
    can_cancel: bool = False


class KnowledgeVersionResponse(BaseModel):
    id: str
    knowledge_base_id: str
    parent_version_id: Optional[str] = None
    build_id: Optional[str] = None
    state: KnowledgeVersionState
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    degraded_reasons: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    published_at: Optional[datetime] = None
    is_active: bool = False
    is_published: bool = False
    is_candidate: bool = False
    build: Optional[KnowledgeBuildResponse] = None


class ListKnowledgeVersionsResponse(BaseModel):
    knowledge_base_id: str
    active_version_id: Optional[str] = None
    active_build: Optional[KnowledgeBuildResponse] = None
    versions: List[KnowledgeVersionResponse] = Field(default_factory=list)
