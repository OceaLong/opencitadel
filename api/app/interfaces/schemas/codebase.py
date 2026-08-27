from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.domain.execution.run import RunStatus
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


class CreateCodebaseRequest(BaseModel):
    name: str = "未命名代码库"
    source_type: CodebaseSourceType = CodebaseSourceType.FILES
    file_id: str | None = None
    git_url: str | None = None
    file_ids: list[str] | None = Field(default_factory=list)

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
    language_stats: dict[str, int] = Field(default_factory=dict)
    file_count: int = 0
    sandbox_id: str | None = None
    workspace_path: str = ""
    error: str | None = None
    vector_degraded: bool = False
    active_version_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ListCodebasesResponse(BaseModel):
    codebases: list[CodebaseResponse]


class FileTreeResponse(BaseModel):
    tree: list[FileTreeNode]


class SymbolResponse(BaseModel):
    id: str
    name: str
    kind: SymbolKind
    file_id: str
    path: str = ""
    signature: str = ""
    start_line: int = 0
    end_line: int = 0
    parent_id: str | None = None


class ListSymbolsResponse(BaseModel):
    symbols: list[SymbolResponse]


class ArtifactResponse(BaseModel):
    id: str
    version_id: str | None = None
    kind: ArtifactKind
    format: ArtifactFormat
    title: str
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ListArtifactsResponse(BaseModel):
    artifacts: list[ArtifactResponse]


class ReadSourceRequest(BaseModel):
    path: str
    start_line: int | None = None
    end_line: int | None = None


class ReadSourceResponse(BaseModel):
    path: str
    content: str
    start_line: int | None = None
    end_line: int | None = None


class CodebaseBuildResponse(BaseModel):
    id: str
    run_id: str | None = None
    codebase_id: str
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


class CodebaseVersionResponse(BaseModel):
    id: str
    codebase_id: str
    parent_version_id: str | None = None
    build_id: str
    state: CodebaseVersionState
    source_snapshot_key: str | None = None
    source_revision: str = ""
    source_digest: str = ""
    capabilities: dict[str, Any] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    published_at: datetime | None = None
    is_active: bool = False
    is_published: bool = False
    is_candidate: bool = False
    build: CodebaseBuildResponse | None = None


class ListCodebaseVersionsResponse(BaseModel):
    codebase_id: str
    active_version_id: str | None = None
    active_build: CodebaseBuildResponse | None = None
    versions: list[CodebaseVersionResponse] = Field(default_factory=list)


class CreateCodebaseSessionRequest(BaseModel):
    mode: SessionMode = SessionMode.ASK
    model_id: str | None = None
    skill_id: str | None = None
    codebase_version_id: str | None = None


class CreateCodebaseSessionResponse(BaseModel):
    session_id: str
    codebase_id: str
    mode: SessionMode


class DownloadCodebaseResponse(BaseModel):
    snapshot_key: str
    download_url: str = ""
