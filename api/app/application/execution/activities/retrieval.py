"""Durable retrieval Activity for Ask-mode context assembly."""

import json

from app.application.execution import activity_types
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.tool_catalog import ExecutionToolCatalog
from app.application.ports.inference_dispatch import auxiliary_activity_context
from app.application.services.memory_service import MemoryService
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)


class RetrievalActivityHandler:
    activity_type = activity_types.RETRIEVAL_SEARCH
    idempotent = True

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        tools: ExecutionToolCatalog,
        memories: MemoryService,
        replay=None,
        isolated=None,
        execution_usage=None,
    ) -> None:
        self._execution_usage = execution_usage
        self._replay = replay
        self._isolated = isolated
        self._objects = objects
        self._tools = tools
        self._memories = memories

    async def execute(self, request, context):
        with auxiliary_activity_context(self._execution_usage, request, context):
            return await self._execute(request, context)

    async def _execute(
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
        try:
            if self._replay is not None and await self._replay.active(context):
                memory_context = None
                result = await self._replay.retrieval(request, context, query)
            elif self._isolated is not None and await self._isolated.active(context):
                memory_context = None
                result = await self._isolated.retrieval(context, query)
            elif getattr(context.run, "source_entity_type", None) == "evaluation_isolated_case":
                raise ValueError("environment_binding_missing")
            else:
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
        except ReplayMismatch:
            return ActivityOutcome.failed(failure_code=ReplayMismatch.code)
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
