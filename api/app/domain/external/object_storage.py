from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ObjectNotFoundError(FileNotFoundError):
    """The provider definitively reports that the requested object key is absent."""


@dataclass(frozen=True)
class BoundedObjectBytes:
    data: bytes
    truncated: bool


@runtime_checkable
class ObjectStoragePort(Protocol):
    """Raw byte object storage (e.g. checkpoint snapshots)."""

    async def put_bytes(self, key: str, data: bytes) -> None: ...

    async def get_bytes(self, key: str) -> bytes:
        """Read bytes; definite absent keys raise ObjectNotFoundError.

        Authorization, missing buckets and transport/service failures retain their
        original classifications and must not be normalized as missing objects.
        """
        ...

    async def get_bounded_bytes(self, key: str, limit: int) -> BoundedObjectBytes:
        """Read at most limit+1 bytes; explicit truncation, never whole-body fallback."""
        ...

    async def delete_bytes(self, key: str) -> None: ...
