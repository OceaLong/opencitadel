#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Optional, Protocol, Tuple

from app.domain.models.codebase import (
    Codebase,
    CodebaseArtifact,
    CodebaseChunk,
    CodebaseEdge,
    CodebaseFile,
    CodebaseStatus,
    CodebaseSymbol,
    ArtifactKind,
)


class CodebaseRepository(Protocol):
    async def save(self, codebase: Codebase) -> None:
        ...

    async def get_by_id(self, codebase_id: str) -> Optional[Codebase]:
        ...

    async def list_all(self, limit: int = 100, offset: int = 0) -> List[Codebase]:
        ...

    async def delete_by_id(self, codebase_id: str) -> None:
        ...

    async def update_status(
            self,
            codebase_id: str,
            status: CodebaseStatus,
            error: Optional[str] = None,
    ) -> None:
        ...

    async def save_files(self, files: List[CodebaseFile]) -> None:
        ...

    async def list_files(self, codebase_id: str) -> List[CodebaseFile]:
        ...

    async def get_file_by_path(self, codebase_id: str, path: str) -> Optional[CodebaseFile]:
        ...

    async def save_symbols(self, symbols: List[CodebaseSymbol]) -> None:
        ...

    async def list_symbols(self, codebase_id: str, name: Optional[str] = None) -> List[CodebaseSymbol]:
        ...

    async def find_symbol_by_name(self, codebase_id: str, name: str) -> List[CodebaseSymbol]:
        ...

    async def save_edges(self, edges: List[CodebaseEdge]) -> None:
        ...

    async def list_edges(
            self,
            codebase_id: str,
            src_symbol_id: Optional[str] = None,
            dst_symbol_id: Optional[str] = None,
            callee_name: Optional[str] = None,
    ) -> List[CodebaseEdge]:
        ...

    async def list_symbols_by_ids(
            self,
            codebase_id: str,
            symbol_ids: List[str],
    ) -> List[CodebaseSymbol]:
        ...

    async def save_chunks(self, chunks: List[CodebaseChunk]) -> None:
        ...

    async def search_chunks(
            self,
            codebase_id: str,
            query_embedding: List[float],
            limit: int = 10,
    ) -> List[Tuple[CodebaseChunk, float]]:
        ...

    async def save_artifacts(self, artifacts: List[CodebaseArtifact]) -> None:
        ...

    async def list_artifacts(self, codebase_id: str, kind: Optional[ArtifactKind] = None) -> List[CodebaseArtifact]:
        ...

    async def clear_analysis_data(self, codebase_id: str) -> None:
        ...
