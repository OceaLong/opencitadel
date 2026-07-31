#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Task 6 RED/GREEN contracts for bounded, resumable versioned GraphRAG."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from app.domain.models.app_config import AppConfig
from app.domain.models.knowledge_base import ChunkLevel, KnowledgeChunk
from app.domain.services.knowledge_base.chunker import KBChunker
from app.domain.services.knowledge_base.parsers import PageBlock
from app.domain.services.knowledge_base.graph_builder import (
    GraphBudget,
    GraphBuilder,
    normalize_entity_name,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _payload(name: str = "OpenCitadel", entity_type: str = "product") -> str:
    return json.dumps(
        {
            "entities": [
                {
                    "name": name,
                    "type": entity_type,
                    "description": "knowledge system",
                },
                {
                    "name": "RAG",
                    "type": "concept",
                    "description": "retrieval",
                },
            ],
            "relations": [
                {"src": name, "dst": "RAG", "relation": "uses"},
            ],
        }
    )


class _Parser:
    async def invoke(self, text, default_value=None):
        try:
            return json.loads(text)
        except Exception:
            return default_value


class _LLM:
    def __init__(
        self,
        *,
        delay: float = 0,
        payload_for_call: Callable[[int], str] | None = None,
        tokens: int = 3,
    ):
        self.delay = delay
        self.payload_for_call = payload_for_call or (lambda _index: _payload())
        self.tokens = tokens
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def invoke(self, messages):
        index = self.calls
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return {
                "content": self.payload_for_call(index),
                "usage": {"total_tokens": self.tokens},
            }
        finally:
            self.active -= 1


class _Repo:
    def __init__(self, *, fail_on_chunk: str | None = None):
        self.fail_on_chunk = fail_on_chunk
        self.persisted_chunk_ids: list[str] = []
        self.entities: dict[tuple[str, str], object] = {}
        self.relations: dict[str, object] = {}
        self.refs: dict[str, object] = {}

    async def upsert_candidate_graph_batch(
        self, kb_id, version_id, entities, relations, refs
    ):
        chunk_ids = {
            relation.chunk_id for relation in relations if relation.chunk_id
        }
        if self.fail_on_chunk and self.fail_on_chunk in chunk_ids:
            raise RuntimeError("injected graph persistence failure")
        self.persisted_chunk_ids.extend(sorted(chunk_ids))
        for entity in entities:
            self.entities[(entity.normalized_name, entity.type)] = entity
        for relation in relations:
            self.relations[relation.id] = relation
        for ref in refs:
            self.refs[ref.id] = ref

    async def get_candidate_index_metrics(self, kb_id, version_id):
        return {
            "entity_count": len(self.entities),
            "relation_count": len(self.relations),
            "entity_ref_count": len(self.refs),
        }


class _Uow:
    def __init__(self, repo):
        self.knowledge_base = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _chunks(count: int, *, version_id: str = "v1") -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            id=f"chunk-{index:03d}",
            kb_id="kb1",
            version_id=version_id,
            doc_id=f"doc-{index // 4:03d}",
            level=ChunkLevel.PARENT,
            content=f"content {index}",
            ordinal=index % 4,
        )
        for index in range(count)
    ]


def _builder(repo: _Repo, llm: _LLM, *, concurrency: int = 2) -> GraphBuilder:
    return GraphBuilder(
        uow_factory=lambda: _Uow(repo),
        llm=llm,
        json_parser=_Parser(),
        concurrency=concurrency,
        max_parent_chunks_per_doc=100,
    )


@pytest.mark.anyio
async def test_version_identity_is_required_and_mixed_chunks_fail_closed():
    builder = _builder(_Repo(), _LLM())
    with pytest.raises(ValueError, match="version"):
        await builder.build(
            "kb1",
            _chunks(1, version_id="foreign"),
            version_id="v1",
            budget=GraphBudget(),
        )


def test_normalization_is_unicode_case_and_space_stable_but_type_is_distinct():
    assert normalize_entity_name("  ＯpenＣitadel  ") == "opencitadel"
    assert normalize_entity_name("OpenCitadel") == normalize_entity_name(
        "opencitadel"
    )


@pytest.mark.anyio
async def test_queue_is_exactly_twice_concurrency_and_live_calls_are_bounded(
    monkeypatch,
):
    observed_maxsizes: list[int] = []
    real_queue = asyncio.Queue

    def recording_queue(*args, **kwargs):
        maxsize = kwargs.get("maxsize", args[0] if args else 0)
        observed_maxsizes.append(maxsize)
        return real_queue(*args, **kwargs)

    monkeypatch.setattr(
        "app.domain.services.knowledge_base.graph_builder.asyncio.Queue",
        recording_queue,
    )
    repo = _Repo()
    llm = _LLM(delay=0.01)
    result = await _builder(repo, llm, concurrency=3).build(
        "kb1",
        _chunks(12),
        version_id="v1",
        budget=GraphBudget(
            max_chunks=12,
            max_llm_calls=12,
            max_tokens=100_000,
            deadline_seconds=2,
        ),
    )
    assert observed_maxsizes[0] == 6
    assert llm.max_active <= 3
    assert result.complete is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("budget", "expected_calls"),
    [
        (GraphBudget(max_chunks=2), 2),
        (GraphBudget(max_llm_calls=3), 3),
        (GraphBudget(max_tokens=6), 0),
    ],
)
async def test_each_admission_budget_stops_truthfully(budget, expected_calls):
    repo = _Repo()
    llm = _LLM(tokens=3)
    result = await _builder(repo, llm).build(
        "kb1",
        _chunks(8),
        version_id="v1",
        budget=budget,
    )
    assert result.calls == expected_calls
    assert result.processed == expected_calls
    assert result.degraded_reason == "GRAPH_PARTIAL"
    assert result.complete is False


@pytest.mark.anyio
async def test_expired_durable_deadline_performs_zero_provider_calls():
    llm = _LLM()
    result = await _builder(_Repo(), llm).build(
        "kb1",
        _chunks(2),
        version_id="v1",
        budget=GraphBudget(deadline_seconds=30),
        deadline_utc=(
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat(),
    )

    assert llm.calls == 0
    assert result.calls == 0
    assert result.degraded_reason == "GRAPH_PARTIAL"


@pytest.mark.anyio
async def test_call_and_worst_case_tokens_are_checkpointed_before_invoke():
    checkpoints: list[dict] = []

    class _AdmissionAwareLLM(_LLM):
        @property
        def max_tokens(self) -> int:
            return 128

        async def invoke(self, messages):
            assert checkpoints
            assert checkpoints[-1]["graph_llm_call_count"] == 1
            assert checkpoints[-1]["graph_reserved_token_count"] > 128
            return await super().invoke(messages)

    async def checkpoint(metrics: dict) -> None:
        checkpoints.append(dict(metrics))

    result = await _builder(_Repo(), _AdmissionAwareLLM()).build(
        "kb1",
        _chunks(1),
        version_id="v1",
        budget=GraphBudget(max_tokens=10_000),
        checkpoint=checkpoint,
    )

    assert result.complete is True
    assert checkpoints[-1]["graph_llm_call_count"] == 1
    assert checkpoints[-1]["graph_reserved_token_count"] > 128
    assert checkpoints[-1]["graph_actual_token_count"] == 3


@pytest.mark.anyio
async def test_too_small_worst_case_token_budget_makes_zero_calls():
    llm = _LLM()
    result = await _builder(_Repo(), llm).build(
        "kb1",
        _chunks(1),
        version_id="v1",
        budget=GraphBudget(max_tokens=10),
    )

    assert llm.calls == 0
    assert result.calls == 0
    assert result.degraded_reason == "GRAPH_PARTIAL"


@pytest.mark.anyio
async def test_configured_output_limit_is_not_truncated_during_reservation():
    class _LargeOutputLLM(_LLM):
        @property
        def max_tokens(self) -> int:
            return 20_000

    llm = _LargeOutputLLM()
    result = await _builder(_Repo(), llm).build(
        "kb1",
        _chunks(1),
        version_id="v1",
        budget=GraphBudget(max_tokens=19_999),
    )

    assert llm.calls == 0
    assert result.calls == 0
    assert result.degraded_reason == "GRAPH_PARTIAL"


@pytest.mark.anyio
async def test_missing_provider_usage_is_normalized_to_integer_actual_tokens():
    checkpoints: list[dict] = []

    class _NoUsageLLM(_LLM):
        async def invoke(self, messages):
            self.calls += 1
            return {"content": _payload()}

    async def checkpoint(metrics: dict) -> None:
        checkpoints.append(dict(metrics))

    result = await _builder(_Repo(), _NoUsageLLM()).build(
        "kb1",
        _chunks(1),
        version_id="v1",
        budget=GraphBudget(max_tokens=10_000),
        checkpoint=checkpoint,
    )

    assert isinstance(result.tokens, int)
    assert result.tokens > 0
    assert isinstance(
        checkpoints[-1]["graph_actual_token_count"],
        int,
    )


@pytest.mark.anyio
async def test_parser_failure_preserves_known_provider_usage():
    checkpoints: list[dict] = []

    class _FailingParser:
        async def invoke(self, text, default_value=None):
            del text, default_value
            raise RuntimeError("injected parser failure")

    async def checkpoint(metrics: dict) -> None:
        checkpoints.append(dict(metrics))

    llm = _LLM(tokens=37)
    result = await GraphBuilder(
        uow_factory=lambda: _Uow(_Repo()),
        llm=llm,
        json_parser=_FailingParser(),
        concurrency=1,
        max_parent_chunks_per_doc=100,
    ).build(
        "kb1",
        _chunks(1),
        version_id="v1",
        budget=GraphBudget(max_tokens=10_000),
        checkpoint=checkpoint,
    )

    assert llm.calls == 1
    assert result.failed == 1
    assert result.tokens == 37
    assert checkpoints[-1]["graph_actual_token_count"] == 37
    assert checkpoints[-1]["graph_cursor"] is None


@pytest.mark.anyio
async def test_out_of_order_overage_is_checkpointed_before_prefix_unblocks():
    release_first = asyncio.Event()
    overage_checkpointed = asyncio.Event()
    checkpoints: list[dict] = []

    class _OutOfOrderLLM(_LLM):
        async def invoke(self, messages):
            index = self.calls
            self.calls += 1
            if index == 0:
                await release_first.wait()
                return {
                    "content": _payload(),
                    "usage": {"total_tokens": 3},
                }
            return {
                "content": _payload(),
                "usage": {"total_tokens": 50_000},
            }

    async def checkpoint(metrics: dict) -> None:
        checkpoints.append(dict(metrics))
        if (
            metrics["graph_actual_token_count"] >= 50_000
            and metrics["graph_processed_count"] == 0
        ):
            overage_checkpointed.set()
            release_first.set()

    result = await asyncio.wait_for(
        _builder(_Repo(), _OutOfOrderLLM(), concurrency=2).build(
            "kb1",
            _chunks(2),
            version_id="v1",
            budget=GraphBudget(max_tokens=10_000),
            checkpoint=checkpoint,
        ),
        timeout=1,
    )

    assert overage_checkpointed.is_set()
    pre_overage_checkpoints = [
        item
        for item in checkpoints
        if item["graph_actual_token_count"] < 50_000
    ]
    assert all(
        item["graph_reserved_token_count"] <= 10_000
        for item in pre_overage_checkpoints
    )
    assert any(
        item["graph_actual_token_count"] >= 50_000
        and item["graph_cursor"] is None
        and item["graph_processed_count"] == 0
        for item in checkpoints
    )
    assert result.degraded_reason == "GRAPH_PARTIAL"


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["failure", "invalid", "overage"])
async def test_blocked_outcomes_checkpoint_consumption_without_cursor_advance(
    outcome,
):
    checkpoints: list[dict] = []

    class _FailingLLM(_LLM):
        async def invoke(self, messages):
            self.calls += 1
            raise RuntimeError("injected extraction failure")

    if outcome == "failure":
        llm = _FailingLLM()
        budget = GraphBudget(
            max_llm_calls=1,
            max_tokens=10_000,
        )
    elif outcome == "invalid":
        llm = _LLM(payload_for_call=lambda _index: "{}")
        budget = GraphBudget(
            max_llm_calls=1,
            max_tokens=10_000,
        )
    else:
        llm = _LLM(tokens=50_000)
        budget = GraphBudget(
            max_llm_calls=1,
            max_tokens=10_000,
        )

    async def checkpoint(metrics: dict) -> None:
        checkpoints.append(dict(metrics))

    result = await _builder(_Repo(), llm, concurrency=1).build(
        "kb1",
        _chunks(2),
        version_id="v1",
        budget=budget,
        checkpoint=checkpoint,
    )

    assert checkpoints
    assert checkpoints[-1]["graph_llm_call_count"] == 1
    assert checkpoints[-1]["graph_reserved_token_count"] > 0
    assert checkpoints[-1]["graph_cursor"] is None
    if outcome == "overage":
        assert checkpoints[-1]["graph_actual_token_count"] == 50_000
        assert result.calls == 1

    retry_llm = _LLM()
    retry = await _builder(_Repo(), retry_llm, concurrency=1).build(
        "kb1",
        _chunks(2),
        version_id="v1",
        budget=GraphBudget(max_llm_calls=1),
        consumed_llm_calls=checkpoints[-1]["graph_llm_call_count"],
        consumed_tokens=checkpoints[-1][
            "graph_reserved_token_count"
        ],
    )
    assert retry_llm.calls == 0
    assert retry.calls == 0


@pytest.mark.anyio
async def test_deadline_cancels_and_drains_workers_without_late_writes():
    repo = _Repo()
    llm = _LLM(delay=0.2)
    result = await _builder(repo, llm).build(
        "kb1",
        _chunks(8),
        version_id="v1",
        budget=GraphBudget(deadline_seconds=0.03),
    )
    writes_at_return = list(repo.persisted_chunk_ids)
    await asyncio.sleep(0.05)
    assert repo.persisted_chunk_ids == writes_at_return
    assert llm.active == 0
    assert result.degraded_reason == "GRAPH_PARTIAL"
    assert result.complete is False


@pytest.mark.anyio
async def test_external_cancellation_awaits_workers_and_prevents_late_effects():
    repo = _Repo()
    llm = _LLM(delay=0.2)
    checkpoints: list[dict] = []

    async def checkpoint(metrics: dict) -> None:
        checkpoints.append(dict(metrics))

    task = asyncio.create_task(
        _builder(repo, llm, concurrency=2).build(
            "kb1",
            _chunks(4),
            version_id="v1",
            budget=GraphBudget(max_tokens=100_000),
            checkpoint=checkpoint,
        )
    )
    while llm.active < 2:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    calls_at_cancel = llm.calls
    writes_at_cancel = list(repo.persisted_chunk_ids)
    checkpoints_at_cancel = list(checkpoints)
    assert llm.active == 0
    await asyncio.sleep(0.25)
    assert llm.calls == calls_at_cancel
    assert repo.persisted_chunk_ids == writes_at_cancel
    assert checkpoints == checkpoints_at_cancel


@pytest.mark.anyio
async def test_durable_cursor_resume_never_reprocesses_checkpointed_chunks():
    repo = _Repo()
    checkpoints: list[dict] = []

    async def checkpoint(metrics: dict) -> None:
        checkpoints.append(dict(metrics))

    first = await _builder(repo, _LLM()).build(
        "kb1",
        _chunks(5),
        version_id="v1",
        budget=GraphBudget(max_chunks=2),
        checkpoint=checkpoint,
    )
    first_ids = list(repo.persisted_chunk_ids)
    assert first.cursor
    assert checkpoints[-1]["graph_cursor"] == first.cursor
    assert checkpoints[-1]["graph_processed_count"] == 2
    assert checkpoints[-1]["graph_llm_call_count"] == 2
    assert checkpoints[-1]["graph_token_count"] == 6

    second = await _builder(repo, _LLM()).build(
        "kb1",
        _chunks(5),
        version_id="v1",
        budget=GraphBudget(),
        resume_cursor=first.cursor,
        checkpoint=checkpoint,
    )
    assert not set(first_ids).intersection(
        repo.persisted_chunk_ids[len(first_ids) :]
    )
    assert second.complete is True
    assert set(repo.persisted_chunk_ids) == {
        f"chunk-{index:03d}" for index in range(5)
    }


@pytest.mark.anyio
async def test_resume_admission_honors_durable_build_level_counters():
    repo = _Repo()
    first = await _builder(repo, _LLM(), concurrency=1).build(
        "kb1",
        _chunks(5),
        version_id="v1",
        budget=GraphBudget(max_chunks=2),
    )
    resumed_llm = _LLM()
    resumed = await _builder(repo, resumed_llm, concurrency=1).build(
        "kb1",
        _chunks(5),
        version_id="v1",
        budget=GraphBudget(max_chunks=2),
        resume_cursor=first.cursor,
        consumed_chunks=first.processed,
        consumed_llm_calls=first.calls,
        consumed_tokens=first.tokens,
    )

    assert resumed_llm.calls == 0
    assert resumed.calls == 0
    assert resumed.cursor == first.cursor
    assert resumed.degraded_reason == "GRAPH_PARTIAL"
    assert resumed.complete is False


@pytest.mark.anyio
async def test_cursor_never_advances_past_an_invalid_middle_chunk():
    repo = _Repo()
    first = await _builder(
        repo,
        _LLM(
            payload_for_call=lambda index: (
                "{}" if index == 1 else _payload()
            )
        ),
        concurrency=1,
    ).build(
        "kb1",
        _chunks(3),
        version_id="v1",
        budget=GraphBudget(),
    )
    assert first.invalid == 1
    assert first.cursor
    persisted_before_resume = list(repo.persisted_chunk_ids)

    await _builder(repo, _LLM(), concurrency=1).build(
        "kb1",
        _chunks(3),
        version_id="v1",
        budget=GraphBudget(),
        resume_cursor=first.cursor,
    )
    resumed = repo.persisted_chunk_ids[len(persisted_before_resume) :]
    assert resumed[0] == "chunk-001"


@pytest.mark.anyio
async def test_unexpected_provider_token_overage_stops_new_admission():
    repo = _Repo()
    llm = _LLM(tokens=5_000)
    result = await _builder(repo, llm, concurrency=1).build(
        "kb1",
        _chunks(5),
        version_id="v1",
        budget=GraphBudget(max_tokens=1_000),
    )
    assert result.calls == 1
    assert result.tokens == 5_000
    assert result.cursor is None
    assert result.degraded_reason == "GRAPH_PARTIAL"
    assert repo.persisted_chunk_ids == []


@pytest.mark.anyio
async def test_per_document_cap_is_truthful_partial():
    repo = _Repo()
    result = await GraphBuilder(
        uow_factory=lambda: _Uow(repo),
        llm=_LLM(),
        json_parser=_Parser(),
        concurrency=1,
        max_parent_chunks_per_doc=1,
    ).build(
        "kb1",
        _chunks(4),
        version_id="v1",
        budget=GraphBudget(),
    )
    assert result.skipped == 3
    assert result.degraded_reason == "GRAPH_PARTIAL"
    assert result.complete is False


def test_parent_and_child_chunk_ids_are_retry_stable_and_version_isolated():
    chunker = KBChunker()
    blocks = [PageBlock(text="alpha beta", page_no=1, heading_path="H")]
    first_parents = chunker._build_parent_chunks(
        "kb1", "doc1", blocks, version_id="v1"
    )
    second_parents = chunker._build_parent_chunks(
        "kb1", "doc1", blocks, version_id="v1"
    )
    other_parents = chunker._build_parent_chunks(
        "kb1", "doc1", blocks, version_id="v2"
    )
    first_children = chunker._build_child_chunks(
        "kb1", "doc1", first_parents, version_id="v1"
    )
    second_children = chunker._build_child_chunks(
        "kb1", "doc1", second_parents, version_id="v1"
    )
    other_children = chunker._build_child_chunks(
        "kb1", "doc1", other_parents, version_id="v2"
    )

    assert [item.id for item in first_parents] == [
        item.id for item in second_parents
    ]
    assert [item.id for item in first_children] == [
        item.id for item in second_children
    ]
    assert first_parents[0].id != other_parents[0].id
    assert first_children[0].id != other_children[0].id
    assert first_children[0].parent_id == first_parents[0].id


def test_graph_budget_config_seed_and_schema_roundtrip():
    seed = yaml.safe_load(
        (Path(__file__).parents[5] / "config.yaml").read_text()
    )
    graph_seed = seed["knowledge_base"]["graphrag"]
    assert graph_seed == {
        "enabled": True,
        "max_parent_chunks_per_doc": 200,
        "concurrency": 3,
        "max_chunks": 10000,
        "max_llm_calls": 10000,
        "max_tokens": 1000000,
        "deadline_seconds": 300,
    }
    config = AppConfig.model_validate(seed)
    roundtrip = AppConfig.model_validate(
        config.model_dump(mode="python")
    )
    assert roundtrip.knowledge_base.graphrag.model_dump() == graph_seed


@pytest.mark.anyio
async def test_wrong_version_or_malformed_resume_cursor_is_rejected():
    builder = _builder(_Repo(), _LLM())
    first = await builder.build(
        "kb1",
        _chunks(2),
        version_id="v1",
        budget=GraphBudget(max_chunks=1),
    )
    with pytest.raises(ValueError, match="cursor"):
        await builder.build(
            "kb1",
            _chunks(2, version_id="v2"),
            version_id="v2",
            budget=GraphBudget(),
            resume_cursor=first.cursor,
        )
    with pytest.raises(ValueError, match="cursor"):
        await builder.build(
            "kb1",
            _chunks(2),
            version_id="v1",
            budget=GraphBudget(),
            resume_cursor="not-a-cursor",
        )


@pytest.mark.anyio
async def test_resume_after_final_checkpoint_reuses_committed_graph_as_complete():
    repo = _Repo()
    builder = _builder(repo, _LLM(), concurrency=1)
    first = await builder.build(
        "kb1",
        _chunks(2),
        version_id="v1",
        budget=GraphBudget(),
    )
    assert first.complete is True
    second_llm = _LLM()
    second = await _builder(repo, second_llm, concurrency=1).build(
        "kb1",
        _chunks(2),
        version_id="v1",
        budget=GraphBudget(),
        resume_cursor=first.cursor,
    )
    assert second_llm.calls == 0
    assert second.complete is True
    assert second.entity_count == first.entity_count
    assert second.relation_count == first.relation_count


@pytest.mark.anyio
async def test_invalid_payload_and_persistence_failure_never_report_complete():
    invalid = await _builder(
        _Repo(), _LLM(payload_for_call=lambda _index: "{}")
    ).build(
        "kb1",
        _chunks(1),
        version_id="v1",
        budget=GraphBudget(),
    )
    assert invalid.invalid == 1
    assert invalid.complete is False

    failed = await _builder(
        _Repo(fail_on_chunk="chunk-000"), _LLM()
    ).build(
        "kb1",
        _chunks(1),
        version_id="v1",
        budget=GraphBudget(),
    )
    assert failed.persistence_error
    assert failed.complete is False
