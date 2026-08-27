import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models.codebase_version import CodeEvidenceRef
from app.domain.utils.time_utils import utc_now


class CodebaseSourceType(StrEnum):
    ZIP = "zip"
    GIT = "git"
    FILES = "files"


class CodebaseStatus(StrEnum):
    PENDING = "pending"
    MATERIALIZING = "materializing"
    ANALYZING = "analyzing"
    INDEXING = "indexing"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class SymbolKind(StrEnum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    MODULE = "module"
    INTERFACE = "interface"
    VARIABLE = "variable"


class EdgeKind(StrEnum):
    CALL = "call"
    IMPORT = "import"
    INHERIT = "inherit"


class ArtifactKind(StrEnum):
    ARCHITECTURE = "architecture"
    DATA_FLOW = "data_flow"
    MODULE_DIR = "module_dir"
    FLOWCHART = "flowchart"
    CALL_CHAIN = "call_chain"
    OVERVIEW = "overview"


class ArtifactFormat(StrEnum):
    MERMAID = "mermaid"
    MARKDOWN = "markdown"


class SessionMode(StrEnum):
    ASK = "ask"
    AGENT = "agent"


class Codebase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    source_type: CodebaseSourceType = CodebaseSourceType.FILES
    source_ref: str = ""
    status: CodebaseStatus = CodebaseStatus.PENDING
    language_stats: dict[str, int] = Field(default_factory=dict)
    file_count: int = 0
    sandbox_id: str | None = None
    workspace_path: str = "/home/ubuntu/codebase"
    snapshot_key: str | None = None
    error: str | None = None
    vector_degraded: bool = False
    active_version_id: str | None = None
    owner_user_id: str | None = None
    team_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CodebaseFile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: str | None = None
    path: str
    language: str = ""
    size: int = 0
    sha: str = ""


class CodebaseSymbol(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: str | None = None
    file_id: str
    name: str
    qualified_name: str = ""
    kind: SymbolKind = SymbolKind.FUNCTION
    signature: str = ""
    start_line: int = 0
    end_line: int = 0
    parent_id: str | None = None
    parser: str = "regex"
    confidence: float = 0.0


class CodebaseEdge(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: str | None = None
    src_symbol_id: str
    dst_symbol_id: str | None = None
    callee_name: str = ""
    kind: EdgeKind = EdgeKind.CALL
    resolution: str = "unresolved"
    confidence: float = 0.0
    evidence: list[CodeEvidenceRef] = Field(default_factory=list)


class CodebaseChunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: str | None = None
    file_id: str | None = None
    symbol_id: str | None = None
    content: str = ""
    search_text: str = ""
    embedding: list[float] = Field(default_factory=list)


class CodebaseArtifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: str | None = None
    kind: ArtifactKind
    format: ArtifactFormat = ArtifactFormat.MERMAID
    title: str = ""
    content: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class FileTreeNode(BaseModel):
    name: str
    path: str = ""
    is_dir: bool = False
    language: str = ""
    children: list["FileTreeNode"] = Field(default_factory=list)


FileTreeNode.model_rebuild()
