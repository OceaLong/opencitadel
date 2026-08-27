"""Token-owned sandbox reclaim leadership over an injected lease manager."""

from __future__ import annotations

from app.application.ports.coordination import LeaseManagerPort

_LEADER_KEY = "sandbox:reclaim:leader"


class ReclaimCoordinator:
    def __init__(self, *, leases: LeaseManagerPort, worker_id: str) -> None:
        if not worker_id:
            raise ValueError("reclaim worker_id is required")
        self._leases = leases
        self._worker_id = worker_id

    async def try_become_leader(self, lease_seconds: int) -> bool:
        ttl_seconds = max(5, lease_seconds)
        if await self._leases.acquire(
            _LEADER_KEY,
            self._worker_id,
            ttl_seconds=ttl_seconds,
        ):
            return True
        return await self._leases.renew(
            _LEADER_KEY,
            self._worker_id,
            ttl_seconds=ttl_seconds,
        )


__all__ = ["ReclaimCoordinator"]
