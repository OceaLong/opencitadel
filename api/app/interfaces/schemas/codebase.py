#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.models.codebase import (
    ArtifactFormat,
    ArtifactKind,
    CodebaseSourceType,
    CodebaseStatus,
    FileTreeNode,
    SessionMode,
    SymbolKind,
)


class CreateCodebaseRequest(BaseModel):
    name: str = "未命名代码库"
    source_type: CodebaseSourceType = CodebaseSourceType.FILES
    file_id: Optional[str] = None
    git_url: Optional[str] = None
    file_ids: Optional[List[str]] = Field(default_factory=list)


class CodebaseResponse(BaseModel):
    id: str
    name: str
    source_type: CodebaseSourceType
    source_ref: str = ""
    status: CodebaseStatus
    language_stats: Dict[str, int] = Field(default_factory=dict)
    file_count: int = 0
    sandbox_id: Optional[str] = None
    workspace_path: str = ""
    ingest_task_id: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ListCodebasesResponse(BaseModel):
    codebases: List[CodebaseResponse]


class FileTreeResponse(BaseModel):
    tree: List[FileTreeNode]


class SymbolResponse(BaseModel):
    id: str
    name: str
    kind: SymbolKind
    file_id: str
    signature: str = ""
    start_line: int = 0
    end_line: int = 0
    parent_id: Optional[str] = None


class ListSymbolsResponse(BaseModel):
    symbols: List[SymbolResponse]


class ArtifactResponse(BaseModel):
    id: str
    kind: ArtifactKind
    format: ArtifactFormat
    title: str
    content: str
    meta: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ListArtifactsResponse(BaseModel):
    artifacts: List[ArtifactResponse]


class ReadSourceRequest(BaseModel):
    path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None


class ReadSourceResponse(BaseModel):
    path: str
    content: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None


class CreateCodebaseSessionRequest(BaseModel):
    mode: SessionMode = SessionMode.ASK
    model_id: Optional[str] = None
    skill_id: Optional[str] = None


class CreateCodebaseSessionResponse(BaseModel):
    session_id: str
    codebase_id: str
    mode: SessionMode


class DownloadCodebaseResponse(BaseModel):
    snapshot_key: str
    download_url: str = ""
