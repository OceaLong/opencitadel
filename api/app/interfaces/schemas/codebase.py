#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from app.domain.models.codebase import (
    ArtifactFormat,
    ArtifactKind,
    CodebaseSourceType,
    CodebaseStatus,
    FileTreeNode,
    SessionMode,
    SymbolKind,
)
from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.resource_governance import BuildState


class CreateCodebaseRequest(BaseModel):
    name: str = "未命名代码库"
    source_type: CodebaseSourceType = CodebaseSourceType.FILES
    file_id: Optional[str] = None
    git_url: Optional[str] = None
    file_ids: Optional[List[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_payload(self) -> "CreateCodebaseRequest":
        if self.source_type is CodebaseSourceType.ZIP and not self.file_id:
            raise ValueError("zip source requires file_id")
        if self.source_type is CodebaseSourceType.GIT and not self.git_url:
            raise ValueError("git source requires git_url")
        if self.source_type is CodebaseSourceType.FILES and not self.file_ids:
            raise ValueError("files source requires at least one file_id")
        return self


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
    vector_degraded: bool = False
    active_version_id: Optional[str] = None
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
    path: str = ""
    signature: str = ""
    start_line: int = 0
    end_line: int = 0
    parent_id: Optional[str] = None


class ListSymbolsResponse(BaseModel):
    symbols: List[SymbolResponse]


class ArtifactResponse(BaseModel):
    id: str
    version_id: Optional[str] = None
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


class CodebaseBuildResponse(BaseModel):
    id: str
    codebase_id: str
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


class CodebaseVersionResponse(BaseModel):
    id: str
    codebase_id: str
    parent_version_id: Optional[str] = None
    build_id: Optional[str] = None
    state: CodebaseVersionState
    source_snapshot_key: Optional[str] = None
    source_revision: str = ""
    source_digest: str = ""
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    degraded_reasons: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    published_at: Optional[datetime] = None
    is_active: bool = False
    is_published: bool = False
    is_candidate: bool = False
    build: Optional[CodebaseBuildResponse] = None


class ListCodebaseVersionsResponse(BaseModel):
    codebase_id: str
    active_version_id: Optional[str] = None
    active_build: Optional[CodebaseBuildResponse] = None
    versions: List[CodebaseVersionResponse] = Field(default_factory=list)


class CreateCodebaseSessionRequest(BaseModel):
    mode: SessionMode = SessionMode.ASK
    model_id: Optional[str] = None
    skill_id: Optional[str] = None
    codebase_version_id: Optional[str] = None


class CreateCodebaseSessionResponse(BaseModel):
    session_id: str
    codebase_id: str
    mode: SessionMode


class DownloadCodebaseResponse(BaseModel):
    snapshot_key: str
    download_url: str = ""
