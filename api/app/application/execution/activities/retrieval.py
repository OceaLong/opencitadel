"""Durable retrieval Activity for Ask-mode context assembly."""

import json
import re

from app.application.execution import activity_types
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.decisions.context import context_enabled
from app.application.execution.tool_catalog import ExecutionToolCatalog
from app.application.ports.inference_dispatch import auxiliary_activity_context
from app.application.services.memory_service import MemoryService
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.execution.context_resources import ContextResourceEvidence
from app.domain.execution.tool_contract import ToolContractChanged


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
            try:
                return await self._execute(request, context)
            except ValueError as error:
                if re.fullmatch(r"CONTEXT_[A-Z_]{1,80}", str(error)):
                    return ActivityOutcome.failed(failure_code=str(error))
                raise

    async def _execute(
        self,
        request: ActivityRequest,
        context: ActivityContext,
    ) -> ActivityOutcome:
        if request.input_ref is None:
            return ActivityOutcome.failed(failure_code="ACTIVITY_INPUT_MISSING")
        bounded = context_enabled(context.run.definition_manifest)
        load = self._objects.load_input_bounded if bounded else self._objects.load_input
        payload = await load(
            key=request.input_ref,
            expected_digest=request.input_digest,
            **(
                {"limit": context.run.definition_manifest.context_policy.read_bytes}
                if bounded
                else {}
            ),
        )
        query = payload.get("message")
        if not isinstance(query, str) or not query.strip():
            return ActivityOutcome.failed(failure_code="RETRIEVAL_QUERY_INVALID")
        family_policy = context.run.policy_snapshot.family_policy
        if family_policy.kind not in {"agent", "ask"}:
            return ActivityOutcome.failed(failure_code="POLICY_SNAPSHOT_INVALID")
        memory_evidence = None
        replayed = False
        try:
            if self._replay is not None and await self._replay.active(context):
                replayed = True
                memory_context = None
                result = await self._replay.retrieval(request, context, query)
            elif self._isolated is not None and await self._isolated.active(context):
                memory_context = None
                result = await self._isolated.retrieval(context, query)
            elif getattr(context.run, "source_entity_type", None) == "evaluation_isolated_case":
                raise ValueError("environment_binding_missing")
            else:
                if bounded:
                    memory_evidence = await self._memories.recall_context(
                        str(payload.get("session_id") or ""),
                        query=query,
                        owner_scope=context.run.owner_scope,
                        policy=family_policy.memory,
                    )
                    memory_context = memory_evidence.content
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
        except ToolContractChanged:
            return ActivityOutcome.failed(failure_code="CONTEXT_RESOURCE_AUTHORIZATION_REVOKED")
        result = dict(result)
        sources = result.get("sources")
        if not isinstance(sources, list):
            return ActivityOutcome.failed(failure_code="RETRIEVAL_RESULT_INVALID")
        sources = result["sources"] = list(sources)
        resources = ContextResourceEvidence.model_validate(
            result.pop("_context_resource_evidence", {"unresolved_sources": bool(sources)})
        )
        if memory_context:
            sources.insert(0, {"kind": "memory", "content": memory_context})
        content = json.dumps(result, ensure_ascii=False, sort_keys=True)
        body = {
            "kind": "retrieval",
            "message": {"role": "user" if bounded else "system", "content": content},
        }
        if bounded:
            body["resource_evidence"] = resources.model_copy(
                update={
                    "memory": memory_evidence.evidence()
                    if memory_evidence
                    else (resources.memory if replayed else None),
                }
            ).model_dump(mode="json")
            result_ref = await self._objects.put_context_result(
                request.activity_id, body, run=context.run
            )
        else:
            result_ref = await self._objects.put_result(
                request.activity_id, body, run_id=context.run.run_id
            )
        return ActivityOutcome.succeeded(
            result_ref=result_ref,
            result_summary=content[:4096],
        )


__all__ = ["RetrievalActivityHandler"]
