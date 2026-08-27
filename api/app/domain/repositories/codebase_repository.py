from typing import Protocol

from app.domain.models.codebase import (
    ArtifactKind,
    Codebase,
    CodebaseArtifact,
    CodebaseChunk,
    CodebaseEdge,
    CodebaseFile,
    CodebaseStatus,
    CodebaseSymbol,
)
from app.domain.models.scope import OwnerScope


class CodebaseRepository(Protocol):
    async def save(self, codebase: Codebase) -> None: ...

    async def get_by_id(
        self, codebase_id: str, scope: OwnerScope | None = None
    ) -> Codebase | None: ...

    async def list_all(
        self, limit: int = 100, offset: int = 0, scope: OwnerScope | None = None
    ) -> list[Codebase]: ...

    async def delete_by_id(self, codebase_id: str) -> None: ...

    async def update_status(
        self,
        codebase_id: str,
        status: CodebaseStatus,
        error: str | None = None,
    ) -> None: ...

    async def save_files(self, files: list[CodebaseFile]) -> None: ...

    async def list_files(
        self,
        codebase_id: str,
        version_id: str | None = None,
    ) -> list[CodebaseFile]: ...

    async def get_file_by_path(
        self,
        codebase_id: str,
        path: str,
        version_id: str | None = None,
    ) -> CodebaseFile | None: ...

    async def save_symbols(self, symbols: list[CodebaseSymbol]) -> None: ...

    async def list_symbols(
        self,
        codebase_id: str,
        name: str | None = None,
        version_id: str | None = None,
    ) -> list[CodebaseSymbol]: ...

    async def find_symbol_by_name(
        self,
        codebase_id: str,
        name: str,
        version_id: str | None = None,
    ) -> list[CodebaseSymbol]: ...

    async def save_edges(self, edges: list[CodebaseEdge]) -> None: ...

    async def list_edges(
        self,
        codebase_id: str,
        src_symbol_id: str | None = None,
        dst_symbol_id: str | None = None,
        callee_name: str | None = None,
        version_id: str | None = None,
    ) -> list[CodebaseEdge]: ...

    async def list_symbols_by_ids(
        self,
        codebase_id: str,
        symbol_ids: list[str],
        version_id: str | None = None,
    ) -> list[CodebaseSymbol]: ...

    async def save_chunks(self, chunks: list[CodebaseChunk]) -> None: ...

    async def search_chunks(
        self,
        codebase_id: str,
        query_embedding: list[float],
        limit: int = 10,
        version_id: str | None = None,
    ) -> list[tuple[CodebaseChunk, float]]: ...

    async def search_vector(
        self,
        codebase_id: str,
        version_id: str,
        query_embedding: list[float],
        limit: int = 10,
    ) -> list[tuple[CodebaseChunk, float]]: ...

    async def search_lexical(
        self,
        codebase_id: str,
        version_id: str,
        query: str,
        limit: int = 10,
    ) -> list[tuple[CodebaseChunk, float]]: ...

    async def save_artifacts(self, artifacts: list[CodebaseArtifact]) -> None: ...

    async def list_artifacts(
        self,
        codebase_id: str,
        kind: ArtifactKind | None = None,
        version_id: str | None = None,
    ) -> list[CodebaseArtifact]: ...

    async def clear_analysis_data(self, codebase_id: str) -> None: ...

    async def flush(self) -> None: ...
