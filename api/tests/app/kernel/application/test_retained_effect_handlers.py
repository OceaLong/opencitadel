"""Closed-world tests for the five retained Effect handler families."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.kernel.application.effect_worker import EffectClaim, EffectExecutionStatus
from app.kernel.application.retained_effects import (
    FileEffect,
    GovernedToolEffect,
    KnowledgeBuildEffect,
    ModelCallEffect,
    RetrievalEffect,
    build_retained_effect_registry,
)
from app.kernel.domain.types import EffectSafety, OwnerScopeRef, Workflow


def _claim(effect_type: str, request: dict, *, safety=EffectSafety.READ_ONLY):
    return EffectClaim(
        effect_id=UUID(int=8100),
        invocation_id=UUID(int=8101),
        run_id=UUID(int=8102),
        workflow=Workflow.AGENT,
        effect_type=effect_type,
        safety=safety,
        request=request,
        owner_scope=OwnerScopeRef.team("team-1"),
        claim_generation=2,
        timeout_seconds=30,
    )


class Quota:
    def __init__(self) -> None:
        self.checked = 0

    async def assert_model_allowed(self, scope, request):
        self.checked += 1


class Inference:
    def __init__(self) -> None:
        self.keys = []

    async def invoke(self, request, *, idempotency_key):
        self.keys.append(idempotency_key)
        return {"content": "done", "usage": {"input_tokens": 3}, "secret": "drop"}


@pytest.mark.asyncio
async def test_model_call_rechecks_quota_and_uses_invocation_as_idempotency_key() -> None:
    quota = Quota()
    inference = Inference()
    result = await ModelCallEffect(quota=quota, inference=inference).execute(
        _claim("model.call", {"prompt": "hello"})
    )

    assert quota.checked == 1
    assert inference.keys == [str(UUID(int=8101))]
    assert result.status is EffectExecutionStatus.SUCCEEDED
    assert result.payload == {"content": "done", "usage": {"input_tokens": 3}}


class Tools:
    def __init__(self) -> None:
        self.calls = []

    async def invoke(
        self,
        name,
        arguments,
        *,
        capability,
        run_id,
        owner_scope,
        idempotency_key,
    ):
        self.calls.append((name, arguments, capability, run_id, owner_scope, idempotency_key))
        return {"stdout": "ok", "credential": "drop"}


@pytest.mark.asyncio
async def test_tool_handler_enforces_frozen_capability_and_sanitizes_result() -> None:
    tools = Tools()
    handler = GovernedToolEffect(tools=tools)
    claim = _claim(
        "tool.call",
        {
            "name": "shell.run",
            "arguments": {"command": "pwd"},
            "capability": {"name": "shell.run", "result_fields": ["stdout"]},
        },
        safety=EffectSafety.NON_IDEMPOTENT_WRITE,
    )

    result = await handler.execute(claim)

    assert result.payload == {"result": {"stdout": "ok"}}
    assert tools.calls[0][5] == str(UUID(int=8101))
    with pytest.raises(ValueError, match="frozen capability"):
        await handler.execute(
            claim.model_copy(update={"request": {**claim.request, "name": "mcp.other"}})
        )


class Files:
    async def operate(self, request, *, run_id, idempotency_key):
        return b"artifact-bytes"


class Knowledge:
    def __init__(self) -> None:
        self.version_ids = None

    async def retrieve(self, query, *, version_ids):
        self.version_ids = tuple(version_ids)
        return [{"text": "evidence", "private": "drop"}]

    async def advance_build(self, request, *, idempotency_key):
        return {"stage": "chunk", "manifest_digest": "a" * 64}


@pytest.mark.asyncio
async def test_file_digest_retrieval_pinning_and_exact_registry() -> None:
    knowledge = Knowledge()
    file_result = await FileEffect(files=Files()).execute(
        _claim("file.operation", {"operation": "read"})
    )
    retrieval_result = await RetrievalEffect(knowledge=knowledge).execute(
        _claim(
            "knowledge.retrieve",
            {"query": "policy", "knowledge_version_ids": ["version-1"]},
        )
    )
    build_result = await KnowledgeBuildEffect(knowledge=knowledge).execute(
        _claim("knowledge.build", {"stage": "parse"})
    )
    registry = build_retained_effect_registry(
        model=ModelCallEffect(quota=Quota(), inference=Inference()),
        retrieval=RetrievalEffect(knowledge=knowledge),
        tool=GovernedToolEffect(tools=Tools()),
        file=FileEffect(files=Files()),
        knowledge_build=KnowledgeBuildEffect(knowledge=knowledge),
    )

    assert len(file_result.payload["digest"]) == 64
    assert knowledge.version_ids == ("version-1",)
    assert retrieval_result.payload == {
        "matches": [{"text": "evidence"}],
        "prompt": "",
        "tool_catalog": [],
        "knowledge_version_ids": [],
    }
    assert build_result.payload["stage"] == "chunk"
    assert set(registry.effect_types) == {
        "model.call",
        "knowledge.retrieve",
        "tool.call",
        "file.operation",
        "knowledge.build",
    }
