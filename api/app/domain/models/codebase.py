#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.models.codebase_version import CodeEvidenceRef


class CodebaseSourceType(str, Enum):
    ZIP = "zip"
    GIT = "git"
    FILES = "files"


class CodebaseStatus(str, Enum):
    PENDING = "pending"
    MATERIALIZING = "materializing"
    ANALYZING = "analyzing"
    INDEXING = "indexing"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class SymbolKind(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    MODULE = "module"
    INTERFACE = "interface"
    VARIABLE = "variable"


class EdgeKind(str, Enum):
    CALL = "call"
    IMPORT = "import"
    INHERIT = "inherit"


class ArtifactKind(str, Enum):
    ARCHITECTURE = "architecture"
    DATA_FLOW = "data_flow"
    MODULE_DIR = "module_dir"
    FLOWCHART = "flowchart"
    CALL_CHAIN = "call_chain"
    OVERVIEW = "overview"


class ArtifactFormat(str, Enum):
    MERMAID = "mermaid"
    MARKDOWN = "markdown"


class SessionMode(str, Enum):
    ASK = "ask"
    AGENT = "agent"


class Codebase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    source_type: CodebaseSourceType = CodebaseSourceType.FILES
    source_ref: str = ""
    status: CodebaseStatus = CodebaseStatus.PENDING
    language_stats: Dict[str, int] = Field(default_factory=dict)
    file_count: int = 0
    sandbox_id: Optional[str] = None
    workspace_path: str = "/home/ubuntu/codebase"
    snapshot_key: Optional[str] = None
    ingest_task_id: Optional[str] = None
    error: Optional[str] = None
    vector_degraded: bool = False
    active_version_id: Optional[str] = None
    owner_user_id: Optional[str] = None
    team_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class CodebaseFile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: Optional[str] = None
    path: str
    language: str = ""
    size: int = 0
    sha: str = ""


class CodebaseSymbol(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: Optional[str] = None
    file_id: str
    name: str
    qualified_name: str = ""
    kind: SymbolKind = SymbolKind.FUNCTION
    signature: str = ""
    start_line: int = 0
    end_line: int = 0
    parent_id: Optional[str] = None
    parser: str = "regex"
    confidence: float = 0.0


class CodebaseEdge(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: Optional[str] = None
    src_symbol_id: str
    dst_symbol_id: Optional[str] = None
    callee_name: str = ""
    kind: EdgeKind = EdgeKind.CALL
    resolution: str = "unresolved"
    confidence: float = 0.0
    evidence: List[CodeEvidenceRef] = Field(default_factory=list)


class CodebaseChunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: Optional[str] = None
    file_id: Optional[str] = None
    symbol_id: Optional[str] = None
    content: str = ""
    search_text: str = ""
    embedding: List[float] = Field(default_factory=list)


class CodebaseArtifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codebase_id: str
    version_id: Optional[str] = None
    kind: ArtifactKind
    format: ArtifactFormat = ArtifactFormat.MERMAID
    title: str = ""
    content: str = ""
    meta: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)


class FileTreeNode(BaseModel):
    name: str
    path: str = ""
    is_dir: bool = False
    language: str = ""
    children: List["FileTreeNode"] = Field(default_factory=list)


FileTreeNode.model_rebuild()
