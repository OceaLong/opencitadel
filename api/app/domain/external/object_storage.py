from typing import Protocol, runtime_checkable


@runtime_checkable
class ObjectStoragePort(Protocol):
    """Raw byte object storage (e.g. checkpoint snapshots)."""

    async def put_bytes(self, key: str, data: bytes) -> None: ...

    async def get_bytes(self, key: str) -> bytes: ...

    async def delete_bytes(self, key: str) -> None: ...
