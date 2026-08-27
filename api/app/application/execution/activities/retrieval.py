"""Durable retrieval Activity for Ask-mode context assembly."""

import json

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.tool_catalog import ExecutionToolCatalog
from app.application.services.memory_service import MemoryService
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)


class RetrievalActivityHandler:
    activity_type = "retrieval.search"
    idempotent = True

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        tools: ExecutionToolCatalog,
        memories: MemoryService,
    ) -> None:
        self._objects = objects
        self._tools = tools
        self._memories = memories

    async def execute(
        self,
        request: ActivityRequest,
        context: ActivityContext,
    ) -> ActivityOutcome:
        if request.input_ref is None:
            return ActivityOutcome.failed(failure_code="ACTIVITY_INPUT_MISSING")
        payload = await self._objects.load_input(
            key=request.input_ref,
            expected_digest=request.input_digest,
        )
        query = payload.get("message")
        if not isinstance(query, str) or not query.strip():
            return ActivityOutcome.failed(failure_code="RETRIEVAL_QUERY_INVALID")
        family_policy = context.run.policy_snapshot.family_policy
        if family_policy.kind not in {"agent", "ask"}:
            return ActivityOutcome.failed(failure_code="POLICY_SNAPSHOT_INVALID")
        memory_context = await self._memories.recall_for_session(
            str(payload.get("session_id") or ""),
            owner_scope=context.run.owner_scope,
            policy=family_policy.memory,
        )
        result = await self._tools.retrieve(
            payload,
            context,
            query=query,
        )
        sources = result.get("sources")
        if not isinstance(sources, list):
            return ActivityOutcome.failed(failure_code="RETRIEVAL_RESULT_INVALID")
        if memory_context:
            sources.insert(0, {"kind": "memory", "content": memory_context})
        content = json.dumps(result, ensure_ascii=False, sort_keys=True)
        result_ref = await self._objects.put_result(
            request.activity_id,
            {
                "kind": "retrieval",
                "message": {"role": "system", "content": content},
            },
        )
        return ActivityOutcome.succeeded(
            result_ref=result_ref,
            result_summary=content[:4096],
        )


__all__ = ["RetrievalActivityHandler"]
