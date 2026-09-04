"""Durable Activity boundaries for resource candidate publication."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Protocol

from app.application.execution import activity_types
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.services.inference_model_service import InferenceModelService
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.external.llm import LLM
from app.domain.models.build_progress import BuildProgress
from app.domain.models.inference import PLATFORM_EMBEDDING_DIMENSIONS
from app.domain.runtime_policy import KnowledgeBaseExecutionPolicy

logger = logging.getLogger(__name__)


class KnowledgeBuildPipeline(Protocol):
    def run_build(
        self,
        build_id: str,
        *,
        policy: KnowledgeBaseExecutionPolicy,
        embedding_model_id: str | None,
        embedding_dimensions: int | None,
        graph_llm: LLM | None = None,
        ocr_llm: LLM | None = None,
    ) -> AsyncIterator[BuildProgress]: ...
    async def cancel(self, build_id: str) -> None: ...


class _ResourceBuildActivity:
    idempotent = True
    phases: tuple[str, ...] = ()

    def __init__(self, *, objects: ActivityObjectStore) -> None:
        self._objects = objects

    async def _build_input(
        self,
        request: ActivityRequest,
    ) -> tuple[str, str | None, int | None] | None:
        if request.input_ref is None:
            return None
        payload = await self._objects.load_input(
            key=request.input_ref,
            expected_digest=request.input_digest,
        )
        build_id = payload.get("build_id")
        if not isinstance(build_id, str) or not build_id:
            return None
        model_id = payload.get("embedding_model_id")
        dimensions = payload.get("embedding_dimensions")
        if model_id is None and dimensions is None:
            return build_id, None, None
        if (
            not isinstance(model_id, str)
            or not model_id
            or dimensions != PLATFORM_EMBEDDING_DIMENSIONS
        ):
            return None
        return build_id, model_id, dimensions

    async def _consume(
        self,
        request: ActivityRequest,
        context: ActivityContext,
        events: AsyncIterator[BuildProgress],
    ) -> ActivityOutcome:
        failure_code: str | None = None
        failure_message: str | None = None
        progress = 0
        async for event in events:
            if event.kind == "error":
                failure_code = event.failure_code or "RESOURCE_BUILD_FAILED"
                failure_message = event.message
                continue
            if event.kind == "done":
                continue
            if event.kind == "step" and event.phase in self.phases:
                index = self.phases.index(event.phase)
                progress = round(
                    (
                        index
                        + (
                            1
                            if event.status is not None and event.status.value == "completed"
                            else 0
                        )
                    )
                    * 100
                    / len(self.phases)
                )
            if context.report_progress is not None:
                accepted = await context.report_progress(
                    {
                        "kind": event.kind,
                        "phase": event.phase,
                        "status": event.status.value if event.status else None,
                        "progress": progress,
                        "message": event.message,
                    }
                )
                if not accepted:
                    raise asyncio.CancelledError
        if failure_code:
            return ActivityOutcome.failed(failure_code=failure_code)
        result_ref = await self._objects.put_result(
            request.activity_id,
            {"status": "published"},
        )
        return ActivityOutcome.succeeded(
            result_ref=result_ref,
            result_summary=failure_message or "published",
        )


class KnowledgeBuildActivityHandler(_ResourceBuildActivity):
    activity_type = activity_types.KNOWLEDGE_BUILD
    phases = (
        "parse",
        "chunk",
        "keyword_index",
        "vector_index",
        "graph",
        "validate",
        "publish",
    )

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        pipeline: KnowledgeBuildPipeline,
        models: InferenceModelService | None = None,
        client_factory: Callable[..., LLM] | None = None,
    ) -> None:
        super().__init__(objects=objects)
        self._pipeline = pipeline
        self._models = models
        self._client_factory = client_factory

    async def _resolve_build_llms(
        self,
        context: ActivityContext,
        policy: KnowledgeBaseExecutionPolicy,
    ) -> tuple[LLM | None, LLM | None]:
        # GraphRAG and vision OCR need a chat client resolved from the caller's
        # binding. Absent a binding the build degrades (graph_search unavailable)
        # rather than failing the whole ingestion.
        if self._models is None or self._client_factory is None:
            return None, None
        need_graph = policy.graphrag.enabled
        need_ocr = policy.ocr.mode != "off"
        if not need_graph and not need_ocr:
            return None, None
        scope = context.run.owner_scope
        try:
            resolved = await self._models.resolve_chat(None, scope=scope)
            client = self._client_factory(
                resolved,
                policy=context.run.policy_snapshot.common.model_resilience,
                thinking_enabled=False,
                inference_model_service=self._models,
                scope=scope,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb build chat model unavailable; graph/ocr will degrade: %s", exc)
            return None, None
        graph_llm = client if need_graph else None
        ocr_llm = client if (need_ocr and resolved.model.capabilities.vision) else None
        return graph_llm, ocr_llm

    async def execute(
        self,
        request: ActivityRequest,
        context: ActivityContext,
    ) -> ActivityOutcome:
        build_input = await self._build_input(request)
        if build_input is None:
            return ActivityOutcome.failed(failure_code="RESOURCE_BUILD_INPUT_INVALID")
        build_id, embedding_model_id, embedding_dimensions = build_input
        family_policy = context.run.policy_snapshot.family_policy
        if family_policy.kind != "kb_ingest":
            return ActivityOutcome.failed(failure_code="POLICY_SNAPSHOT_INVALID")
        graph_llm, ocr_llm = await self._resolve_build_llms(context, family_policy.knowledge_base)
        try:
            return await self._consume(
                request,
                context,
                self._pipeline.run_build(
                    build_id,
                    policy=family_policy.knowledge_base,
                    embedding_model_id=embedding_model_id,
                    embedding_dimensions=embedding_dimensions,
                    graph_llm=graph_llm,
                    ocr_llm=ocr_llm,
                ),
            )
        except asyncio.CancelledError:
            await self._pipeline.cancel(build_id)
            raise


__all__ = ["KnowledgeBuildActivityHandler"]
