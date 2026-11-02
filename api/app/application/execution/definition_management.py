"""Administrative publication requests; business Run state stays kernel-owned."""

import asyncio
from time import monotonic
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import AppException, ForbiddenError
from app.domain.execution.definition import Digest
from app.domain.execution.family import RunFamily


class DefinitionChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    request_id: UUID
    kind: Literal["head", "retire"]
    family: RunFamily
    target_version: int = Field(ge=1)
    expected_digest: Digest
    reason: str = Field(min_length=1, max_length=2000)


class DefinitionManagementService:
    def __init__(self, store, *, wait_seconds=3):
        self.store, self.wait_seconds = store, wait_seconds

    async def definitions(self, *, limit, offset):
        return await self.store.definitions(limit=limit, offset=offset)

    async def workers(self, *, limit, offset):
        return await self.store.workers(limit=limit, offset=offset)

    async def change(self, principal, request):
        if principal.global_role.value != "admin":
            raise ForbiddenError()
        row = await self.store.submit(principal.user_id, request)
        deadline = monotonic() + self.wait_seconds
        while row["status"] == "pending" and monotonic() < deadline:
            await asyncio.sleep(min(0.1, max(0, deadline - monotonic())))
            row = await self.store.result(request.request_id)
        return self.present(row)

    @staticmethod
    def present(row):
        if row["status"] == "failed":
            result = row["result"]
            status = 403 if result["code"] == "DEFINITION_ACTOR_REVOKED" else 409
            raise AppException(code=status, status_code=status, msg=result["code"], data=result)
        return {
            "request_id": str(row["request_id"]),
            "status": row["status"],
            "result": row["result"],
        }

    async def result(self, request_id):
        return self.present(await self.store.result(request_id))
