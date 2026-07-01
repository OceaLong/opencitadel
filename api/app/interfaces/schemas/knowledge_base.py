#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.models.codebase import SessionMode
from app.domain.models.knowledge_base import DocStatus, KBSourceType, KBStatus


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


class CreateKnowledgeBaseSessionRequest(BaseModel):
    mode: SessionMode = SessionMode.ASK
    model_id: Optional[str] = None
    skill_id: Optional[str] = None


class CreateKnowledgeBaseSessionResponse(BaseModel):
    session_id: str
    knowledge_base_id: str
    mode: SessionMode


class ReadKnowledgeDocumentResponse(BaseModel):
    document: KnowledgeDocumentResponse
    content: str
