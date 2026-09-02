"""Narrow adapters for the five retained external Effect families."""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

from .effect_worker import EffectClaim, EffectExecutionResult, EffectRegistry


class ModelQuotaGate(Protocol):
    async def assert_model_allowed(self, scope, request: dict[str, Any]) -> None: ...


class InferenceGateway(Protocol):
    async def invoke(self, request: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]: ...


class ToolGateway(Protocol):
    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        capability: dict[str, Any],
        run_id: str,
        owner_scope: Any,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


class FileGateway(Protocol):
    async def operate(
        self,
        request: dict[str, Any],
        *,
        run_id: str,
        idempotency_key: str,
    ) -> bytes: ...


class KnowledgeGateway(Protocol):
    async def retrieve(
        self, query: str, *, version_ids: tuple[str, ...]
    ) -> list[dict[str, Any]]: ...

    async def advance_build(
        self, request: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]: ...


def _select(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source[field] for field in fields if field in source}


class ModelCallEffect:
    def __init__(self, *, quota: ModelQuotaGate, inference: InferenceGateway) -> None:
        self._quota = quota
        self._inference = inference

    async def execute(self, claim: EffectClaim) -> EffectExecutionResult:
        request = {
            **claim.request,
            "_run_id": str(claim.run_id),
            "_owner_scope": claim.owner_scope.model_dump(mode="json"),
        }
        await self._quota.assert_model_allowed(claim.owner_scope, request)
        raw = await self._inference.invoke(
            request,
            idempotency_key=str(claim.invocation_id),
        )
        return EffectExecutionResult.succeeded(
            _select(raw, ("content", "tool_calls", "usage", "finish_reason"))
        )


class GovernedToolEffect:
    def __init__(self, *, tools: ToolGateway) -> None:
        self._tools = tools

    async def execute(self, claim: EffectClaim) -> EffectExecutionResult:
        name = str(claim.request.get("name", ""))
        capability = dict(claim.request.get("capability") or {})
        if not name or capability.get("name") != name:
            raise ValueError("tool call does not match its frozen capability")
        raw = await self._tools.invoke(
            name,
            dict(claim.request.get("arguments") or {}),
            capability=capability,
            run_id=str(claim.run_id),
            owner_scope=claim.owner_scope,
            idempotency_key=str(claim.invocation_id),
        )
        result_fields = tuple(str(value) for value in capability.get("result_fields", ()))
        safe_result = _select(raw, result_fields) if result_fields else {"status": "completed"}
        return EffectExecutionResult.succeeded({"result": safe_result})


class FileEffect:
    def __init__(self, *, files: FileGateway) -> None:
        self._files = files

    async def execute(self, claim: EffectClaim) -> EffectExecutionResult:
        content = await self._files.operate(
            claim.request,
            run_id=str(claim.run_id),
            idempotency_key=str(claim.invocation_id),
        )
        digest = hashlib.sha256(content).hexdigest()
        return EffectExecutionResult.succeeded(
            {
                "digest": digest,
                "size": len(content),
                "result": {
                    "digest": digest,
                    "size": len(content),
                    "content": content[:50_000].decode("utf-8", errors="replace"),
                },
            }
        )


class RetrievalEffect:
    def __init__(self, *, knowledge: KnowledgeGateway) -> None:
        self._knowledge = knowledge

    async def execute(self, claim: EffectClaim) -> EffectExecutionResult:
        version_ids = tuple(str(value) for value in claim.request.get("knowledge_version_ids", ()))
        if not version_ids:
            raise ValueError("retrieval requires frozen knowledge version ids")
        matches = await self._knowledge.retrieve(
            str(claim.request.get("query", "")),
            version_ids=version_ids,
        )
        safe_matches = [_select(dict(match), ("text", "citation", "score")) for match in matches]
        continuation = dict(claim.request.get("continuation") or {})
        return EffectExecutionResult.succeeded(
            {
                "matches": safe_matches,
                "prompt": str(continuation.get("prompt", "")),
                "tool_catalog": list(continuation.get("tool_catalog") or []),
                "knowledge_version_ids": list(continuation.get("knowledge_version_ids") or []),
            }
        )


class KnowledgeBuildEffect:
    def __init__(self, *, knowledge: KnowledgeGateway) -> None:
        self._knowledge = knowledge

    async def execute(self, claim: EffectClaim) -> EffectExecutionResult:
        raw = await self._knowledge.advance_build(
            claim.request,
            idempotency_key=str(claim.invocation_id),
        )
        return EffectExecutionResult.succeeded(
            _select(raw, ("stage", "manifest_digest", "metrics", "version_id"))
        )


def build_retained_effect_registry(
    *,
    model: ModelCallEffect,
    retrieval: RetrievalEffect,
    tool: GovernedToolEffect,
    file: FileEffect,
    knowledge_build: KnowledgeBuildEffect,
) -> EffectRegistry:
    return EffectRegistry(
        {
            "model.call": model,
            "knowledge.retrieve": retrieval,
            "tool.call": tool,
            "file.operation": file,
            "knowledge.build": knowledge_build,
        }
    )
