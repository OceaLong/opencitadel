"""Resource candidates execute through one durable Activity protocol."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.execution.activities.resource_build import (
    CodebaseBuildActivityHandler,
    KnowledgeBuildActivityHandler,
)
from app.domain.execution.activity import ActivityContext, ActivityRequest
from app.domain.models.build_progress import (
    BuildProgressStatus,
    build_done,
    build_error,
    build_step,
)
from app.domain.models.inference import (
    ChatModelSettings,
    InferenceCapabilities,
    InferenceEndpoint,
    InferenceModel,
    ResolvedInferenceModel,
)
from app.domain.runtime_policy import (
    CodebaseAnalysisPolicy,
    CodebaseExecutionPolicy,
    ExecutionPolicy,
    KnowledgeBaseExecutionPolicy,
    KnowledgeChunkPolicy,
)
from tests.app.execution_test_support import run_execution_context_for


class Objects:
    def __init__(self) -> None:
        self.results: list[tuple[UUID, dict]] = []

    async def load_input(self, *, key: str, expected_digest: str) -> dict:
        assert key == "input://build"
        assert expected_digest == "a" * 64
        return {
            "build_id": "build-1",
            "embedding_model_id": "embedding-1",
            "embedding_dimensions": 1536,
        }

    async def put_result(self, activity_id: UUID, payload: dict) -> str:
        self.results.append((activity_id, payload))
        return "result://published"


class Pipeline:
    def __init__(self, events) -> None:
        self.events = events
        self.build_ids: list[str] = []
        self.embedding_snapshots: list[tuple[str | None, int | None]] = []
        self.policies: list[KnowledgeBaseExecutionPolicy | CodebaseExecutionPolicy] = []
        self.graph_llms: list[object] = []
        self.ocr_llms: list[object] = []
        self.cancelled: list[str] = []

    async def run_build(
        self,
        build_id: str,
        *,
        policy: KnowledgeBaseExecutionPolicy | CodebaseExecutionPolicy,
        embedding_model_id: str | None,
        embedding_dimensions: int | None,
        graph_llm=None,
        ocr_llm=None,
    ):
        self.build_ids.append(build_id)
        self.policies.append(policy)
        self.embedding_snapshots.append((embedding_model_id, embedding_dimensions))
        self.graph_llms.append(graph_llm)
        self.ocr_llms.append(ocr_llm)
        for event in self.events:
            yield event

    async def cancel(self, build_id: str) -> None:
        self.cancelled.append(build_id)


def request(activity_type: str) -> ActivityRequest:
    return ActivityRequest(
        activity_id=UUID("70000000-0000-0000-0000-000000000001"),
        activity_type=activity_type,
        aggregate_type="run",
        aggregate_id="80000000-0000-0000-0000-000000000001",
        generation=0,
        timeout_at=datetime(2026, 8, 25, tzinfo=UTC),
        input_ref="input://build",
        input_digest="a" * 64,
    )


def context(
    progress: list[dict],
    *,
    family: str,
    policy: ExecutionPolicy | None = None,
) -> ActivityContext:
    async def report(value: dict) -> bool:
        progress.append(value)
        return True

    return ActivityContext(
        worker_id="worker-1",
        claim_generation=1,
        idempotency_key="activity-1",
        owner_user_id="user-1",
        team_id=None,
        run=run_execution_context_for(family, policy=policy),
        report_progress=report,
    )


@pytest.mark.asyncio
async def test_codebase_build_uses_its_actual_phases_and_reaches_100() -> None:
    progress: list[dict] = []
    events = []
    for phase in ("materialize", "analyze", "index", "artifacts"):
        events.extend(
            (
                build_step(phase, f"start {phase}", BuildProgressStatus.STARTED),
                build_step(phase, f"finish {phase}", BuildProgressStatus.COMPLETED),
            )
        )
    events.append(build_done())
    objects = Objects()
    pipeline = Pipeline(events)

    outcome = await CodebaseBuildActivityHandler(
        objects=objects,
        pipeline=pipeline,
    ).execute(request("codebase.build"), context(progress, family="codebase_ingest"))

    assert outcome.status == "succeeded"
    assert outcome.result_ref == "result://published"
    assert pipeline.build_ids == ["build-1"]
    assert pipeline.embedding_snapshots == [("embedding-1", 1536)]
    assert [item["progress"] for item in progress if item["kind"] == "step"] == [
        0,
        25,
        25,
        50,
        50,
        75,
        75,
        100,
    ]
    assert objects.results[0][1] == {"status": "published"}


@pytest.mark.asyncio
async def test_codebase_build_receives_the_admitted_analysis_policy() -> None:
    admitted = CodebaseExecutionPolicy(
        vector_enabled=False,
        analysis=CodebaseAnalysisPolicy(
            max_file_size_bytes=123_456,
            max_files=321,
            chunk_max_chars=789,
            source_read_batch_size=17,
        ),
    )
    pipeline = Pipeline([build_done()])

    outcome = await CodebaseBuildActivityHandler(
        objects=Objects(),
        pipeline=pipeline,
    ).execute(
        request("codebase.build"),
        context(
            [],
            family="codebase_ingest",
            policy=ExecutionPolicy(codebase=admitted),
        ),
    )

    assert outcome.status == "succeeded"
    assert pipeline.policies == [admitted]


@pytest.mark.asyncio
async def test_knowledge_build_receives_the_admitted_chunk_policy() -> None:
    admitted = KnowledgeBaseExecutionPolicy(
        vector_enabled=False,
        chunk=KnowledgeChunkPolicy(
            parent_max_chars=9_000,
            child_max_chars=900,
            overlap=90,
        ),
    )
    pipeline = Pipeline([build_done()])

    outcome = await KnowledgeBuildActivityHandler(
        objects=Objects(),
        pipeline=pipeline,
    ).execute(
        request("knowledge.build"),
        context(
            [],
            family="kb_ingest",
            policy=ExecutionPolicy(knowledge_base=admitted),
        ),
    )

    assert outcome.status == "succeeded"
    assert pipeline.policies == [admitted]


@pytest.mark.asyncio
async def test_resource_build_preserves_pipeline_failure_code() -> None:
    objects = Objects()
    pipeline = Pipeline([build_error(message="candidate invalid", failure_code="CLOSURE_INVALID")])

    outcome = await KnowledgeBuildActivityHandler(
        objects=objects,
        pipeline=pipeline,
    ).execute(request("knowledge.build"), context([], family="kb_ingest"))

    assert outcome.status == "failed"
    assert outcome.failure_code == "CLOSURE_INVALID"
    assert objects.results == []


class _VisionModels:
    async def resolve_chat(self, model_id=None, *, scope):
        return ResolvedInferenceModel(
            model=InferenceModel(
                id="model-1",
                endpoint_id="endpoint-1",
                display_name="chat",
                model_name="provider-model",
                settings=ChatModelSettings(),
                capabilities=InferenceCapabilities(vision=True),
            ),
            endpoint=InferenceEndpoint(
                id="endpoint-1",
                display_name="endpoint",
                credential="secret",
            ),
        )


@pytest.mark.asyncio
async def test_knowledge_build_wires_chat_llm_for_graph_and_ocr() -> None:
    # Regression: the kernel never passed a chat client into the KB pipeline, so
    # GraphRAG and vision OCR silently degraded on every build. With models and a
    # client factory the handler must resolve a client and hand it to run_build.
    sentinel = object()
    pipeline = Pipeline([build_done()])

    outcome = await KnowledgeBuildActivityHandler(
        objects=Objects(),
        pipeline=pipeline,
        models=_VisionModels(),
        client_factory=lambda model, **kwargs: sentinel,
    ).execute(request("knowledge.build"), context([], family="kb_ingest"))

    assert outcome.status == "succeeded"
    assert pipeline.graph_llms == [sentinel]
    assert pipeline.ocr_llms == [sentinel]
