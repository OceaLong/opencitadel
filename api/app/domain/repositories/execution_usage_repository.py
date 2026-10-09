from typing import Protocol


class ExecutionUsageRepository(Protocol):
    async def capture_requester(self, scope, authorization, *, run_id) -> dict: ...
    async def snapshot(self, scope, run_id, body, purpose) -> str: ...
    async def admission_snapshot(self, scope, run_id, body, purpose) -> str: ...
    async def load_snapshot(self, scope, run_id, identity, purpose) -> dict: ...

    async def allocate(
        self,
        scope,
        *,
        run_id,
        activity_id,
        generation,
        claim_generation,
        configuration_id,
        request_snapshot,
        logical_group="invoke:0",
    ) -> str: ...
    async def record(self, scope, identity, fact) -> dict: ...
