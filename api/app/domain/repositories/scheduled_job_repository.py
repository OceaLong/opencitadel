from datetime import datetime
from typing import Protocol

from app.domain.models.scheduled_job import ScheduledJob
from app.domain.models.scope import OwnerScope


class ScheduledJobRepository(Protocol):
    async def save(self, job: ScheduledJob) -> None: ...

    async def get_by_id(
        self,
        job_id: str,
        scope: OwnerScope | None = None,
        *,
        for_update: bool = False,
    ) -> ScheduledJob | None: ...

    async def get_by_webhook_token(self, token: str) -> ScheduledJob | None: ...

    async def list_by_owner(self, owner_user_id: str) -> list[ScheduledJob]: ...

    async def list_for_scope(self, scope: OwnerScope) -> list[ScheduledJob]: ...

    async def list_due(self, now: datetime, limit: int = 20) -> list[ScheduledJob]: ...

    async def list_running(self, limit: int = 100) -> list[ScheduledJob]: ...

    async def get_by_last_run_session_id(self, session_id: str) -> ScheduledJob | None: ...

    async def delete_by_id(self, job_id: str) -> None: ...
