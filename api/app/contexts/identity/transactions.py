"""Identity-owned transactional operations."""

from __future__ import annotations

from typing import Any, Protocol


class IdentityTransaction(Protocol):
    async def get_principal(self, user_id: str) -> dict[str, Any] | None: ...

    async def get_team_role(self, team_id: str, user_id: str) -> str | None: ...

    async def get_quota(self, scope_id: str) -> dict[str, int]: ...

    async def set_quota(self, scope_id: str, quota: dict[str, int]) -> None: ...
