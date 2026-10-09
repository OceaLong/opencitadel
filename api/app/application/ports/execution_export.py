"""Durable export boundaries; all source facts precede acceptance and are immutable."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.domain.models.scope import OwnerScope, Principal


@dataclass(frozen=True)
class ExportLease:
    export_id: str
    token: str
    scope: OwnerScope
    principal: Principal
    expires_at: datetime


@dataclass(frozen=True)
class ExportChunk:
    intent_id: str
    key: str
    ordinal: int
    size: int
    digest: str


class ExportRepository(Protocol):
    async def accept(self, scope, principal, request) -> dict[str, Any]:
        """One transaction: current authorization, receipt, fixed facts, quota, job."""
        ...

    async def claim(self) -> ExportLease | None: ...
    async def renew(self, lease: ExportLease) -> None: ...
    async def header(self, lease: ExportLease) -> dict[str, Any]: ...
    async def page(self, lease: ExportLease, *, after: int, limit: int = 200) -> dict: ...
    async def current(self, scope, principal, export_id: str) -> None:
        """Fresh READ COMMITTED full dependency proof; deny missing original members."""
        ...

    async def begin_chunk(
        self, lease: ExportLease, *, ordinal: int, size: int, digest: str
    ) -> ExportChunk:
        """Commit immutable owned intent before any object I/O."""
        ...

    def chunk_io(self, lease: ExportLease, chunk: ExportChunk) -> AbstractAsyncContextManager[None]:
        """Owned object lock, shared by writer and cleanup; recheck intent and lease."""
        ...

    async def write_completed(self, lease: ExportLease, chunk: ExportChunk) -> None:
        """Acknowledge only this original intent after actual SDK completion.

        This does not publish or require the job's current generation. Unknown
        outcomes remain cleanup-inventoried and retain capture quota.
        """
        ...

    async def publish(
        self, lease: ExportLease, chunks: tuple[ExportChunk, ...], *, size: int, digest: str
    ) -> None:
        """Fresh current authorization and lease/token/expiry check in publish transaction."""
        ...

    async def fail(self, lease: ExportLease, *, code: str) -> None: ...
