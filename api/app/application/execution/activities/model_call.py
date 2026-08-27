"""Durable LLM Activity that emits either a final answer or governed tool intent."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.tool_catalog import ExecutionToolCatalog, ToolDefinition
from app.application.services.file_service import FileService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.skill_service import SkillService
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.execution.commands import JsonValue
from app.domain.external.llm import LLM
from app.domain.models.scope import OwnerScope
from app.domain.services.skills.skill_loader import render_active
from app.domain.services.vision_service import (
    build_user_message,
    prepare_media_attachments_from_files,
)

_MAX_TOOL_CALLS = 16
_MAX_ARGUMENT_BYTES = 64 * 1024


class ModelCallActivityHandler:
    activity_type = "model.call"
    # Provider calls are not assumed idempotent or queryable after a crash.
    idempotent = False

    def __init__(
        self,
        *,
        objects: ActivityObjectStore,
        models: InferenceModelService,
        tools: ExecutionToolCatalog,
        skills: SkillService | None = None,
        token_usage: LLMTokenUsageService | None = None,
        files: FileService | None = None,
        client_factory: Callable[..., LLM],
    ) -> None:
        self._objects = objects
        self._models = models
        self._tools = tools
        self._skills = skills
        self._token_usage = token_usage
        self._files = files
        self._client_factory = client_factory

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
        prompt = payload.get("message")
        if not isinstance(prompt, str) or not prompt.strip():
            return ActivityOutcome.failed(failure_code="MODEL_PROMPT_INVALID")
        history = await self._load_history(request)
        if history is None:
            return ActivityOutcome.failed(failure_code="MODEL_HISTORY_INVALID")
        scope = _owner_scope(context)
        model_id = payload.get("model_id")
        if model_id is not None and not isinstance(model_id, str):
            return ActivityOutcome.failed(failure_code="MODEL_ID_INVALID")
        model = await self._models.resolve_chat(model_id, scope=scope)
        temperature_override = payload.get("temperature_override")
        if temperature_override is not None:
            if (
                not isinstance(temperature_override, (int, float))
                or isinstance(temperature_override, bool)
                or not 0 <= float(temperature_override) <= 2
            ):
                return ActivityOutcome.failed(failure_code="MODEL_TEMPERATURE_INVALID")
            settings = model.model.settings.model_copy(
                update={"temperature": float(temperature_override)}
            )
            model = model.model_copy(
                update={"model": model.model.model_copy(update={"settings": settings})}
            )
        client = self._client_factory(
            model,
            policy=context.run.policy_snapshot.common.model_resilience,
            thinking_enabled=payload.get("thinking_enabled") is True,
            inference_model_service=self._models,
            scope=scope,
        )
        allow_tools = request.input_payload.get("allow_tools") is True
        definitions = await self._tools.definitions(payload, context) if allow_tools else ()
        schemas = [definition.tool_schema for definition in definitions]
        messages = await self._messages(
            payload=payload,
            prompt=prompt,
            history=history,
            scope=scope,
            client=client,
        )
        response = await client.invoke(
            messages,
            tools=schemas or None,
        )
        await self._record_usage(
            request=request,
            context=context,
            payload=payload,
            response=response,
            client=client,
            fallback_model=model,
        )
        content = response.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        normalized = _normalize_tool_calls(
            response.get("tool_calls"),
            definitions,
        )
        if normalized is None:
            return ActivityOutcome.failed(failure_code="MODEL_TOOL_CALL_INVALID")
        provider_calls = [
            {
                "id": item["call_id"],
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": json.dumps(
                        item["arguments"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            }
            for item in normalized
        ]
        message: dict[str, JsonValue] = {
            "role": "assistant",
            "content": content,
        }
        if provider_calls:
            message["tool_calls"] = provider_calls
        result_ref = await self._objects.put_result(
            request.activity_id,
            {"kind": "model", "message": message},
        )
        return ActivityOutcome.succeeded(
            result_ref=result_ref,
            result_summary=content[:4096],
            decision_data={"tool_calls": normalized},
            public_data={
                "kind": "message",
                "role": "assistant",
                "message": content[:65_536],
                "resource_bindings": _public_bindings(payload),
            },
        )

    async def _record_usage(
        self,
        *,
        request: ActivityRequest,
        context: ActivityContext,
        payload: dict[str, JsonValue],
        response: dict[str, Any],
        client: LLM,
        fallback_model,
    ) -> None:
        if self._token_usage is None:
            return
        usage = response.get("_usage")
        session_id = payload.get("session_id")
        if not isinstance(usage, dict) or not isinstance(session_id, str):
            return
        values = {
            key: _usage_integer(usage.get(key))
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "cached_tokens",
                "cache_write_tokens",
            )
        }
        if not values["prompt_tokens"] and not values["completion_tokens"]:
            return
        active_model = getattr(client, "active_model", fallback_model)
        round_index = request.input_payload.get("round", 0)
        await self._token_usage.record(
            session_id=session_id,
            agent=str(payload.get("mode") or "agent"),
            step=f"model:{round_index}",
            model_id=getattr(active_model, "id", None),
            model_name=str(getattr(active_model, "model_name", "")),
            prompt_tokens=values["prompt_tokens"],
            completion_tokens=values["completion_tokens"],
            cached_tokens=values["cached_tokens"],
            cache_write_tokens=values["cache_write_tokens"],
            cache_metric_source=str(usage.get("cache_metric_source") or "provider"),
            owner_user_id=context.owner_user_id,
            team_id=context.team_id,
            call_type="invoke",
        )

    async def _load_history(
        self,
        request: ActivityRequest,
    ) -> list[dict[str, Any]] | None:
        refs = request.input_payload.get("history_refs", [])
        if not isinstance(refs, list) or len(refs) > 64:
            return None
        messages: list[dict[str, Any]] = []
        for ref in refs:
            if not isinstance(ref, str) or not ref:
                return None
            item = await self._objects.load_result(ref)
            message = item.get("message")
            if not isinstance(message, dict):
                return None
            role = message.get("role")
            if role not in {"system", "assistant", "tool"}:
                return None
            messages.append(dict(message))
        return messages

    async def _messages(
        self,
        *,
        payload: dict[str, JsonValue],
        prompt: str,
        history: list[dict[str, Any]],
        scope: OwnerScope,
        client: LLM,
    ) -> list[dict[str, Any]]:
        mode = str(payload.get("mode") or "agent")
        system = (
            "You are OpenCitadel. Answer from durable context and tool results. "
            "Never claim an external action succeeded until its tool result is present."
        )
        if mode == "ask":
            system += " Use only the supplied read-only evidence and cite sources."
        skill_id = payload.get("skill_id")
        if self._skills is not None and isinstance(skill_id, str) and skill_id:
            skill = await self._skills.get_skill(skill_id, scope=scope)
            if skill.enabled:
                system = f"{system}\n\n{render_active(skill)}"
        conversation = payload.get("conversation", [])
        if not isinstance(conversation, list):
            raise TypeError("conversation must be a list")
        if len(conversation) > 100:
            raise ValueError("conversation must be a bounded list")
        prior: list[dict[str, str]] = []
        for item in conversation:
            if not isinstance(item, dict):
                raise TypeError("conversation message must be an object")
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                raise ValueError("conversation message is invalid")
            prior.append({"role": role, "content": content})
        attachment_manifest, media = await self._attachment_context(
            payload,
            scope=scope,
            client=client,
        )
        user_prompt = prompt
        if attachment_manifest:
            user_prompt = f"{prompt}\n\n{attachment_manifest}"
        return [
            {"role": "system", "content": system},
            *prior,
            build_user_message(user_prompt, media, client),
            *history,
        ]

    async def _attachment_context(
        self,
        payload: dict[str, JsonValue],
        *,
        scope: OwnerScope,
        client: LLM,
    ) -> tuple[str, list]:
        raw = payload.get("attachments", [])
        if not isinstance(raw, list):
            raise TypeError("attachments must be a list")
        if len(raw) > 10:
            raise ValueError("attachments must be a bounded list")
        if not raw:
            return "", []
        if self._files is None:
            raise ValueError("attachment service is unavailable")
        files = []
        manifest = [
            "Attached files are mounted in the session sandbox. Read them with file tools when needed:"
        ]
        for item in raw:
            if not isinstance(item, dict):
                raise TypeError("attachment must be an object")
            file_id = item.get("file_id")
            filename = item.get("filename")
            sandbox_path = item.get("sandbox_path")
            if not all(
                isinstance(value, str) and value for value in (file_id, filename, sandbox_path)
            ):
                raise ValueError("attachment metadata is incomplete")
            file = await self._files.get_file_info(str(file_id), scope=scope)
            files.append(file)
            manifest.append(f"- {filename}: {sandbox_path}")
        media = await prepare_media_attachments_from_files(
            files,
            client,
            self._files.file_storage,
        )
        return "\n".join(manifest), media


def _owner_scope(context: ActivityContext) -> OwnerScope:
    if context.owner_user_id is not None:
        return OwnerScope.personal(context.owner_user_id)
    return OwnerScope.team("execution-kernel", context.team_id or "")


def _usage_integer(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _public_bindings(payload: dict[str, JsonValue]) -> list[JsonValue]:
    raw = payload.get("resource_bindings", [])
    if not isinstance(raw, list):
        raise TypeError("resource_bindings must be a list")
    if len(raw) > 8:
        raise ValueError("resource_bindings must be a bounded list")
    bindings: list[JsonValue] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("resource binding must be an object")
        required = ("binding_id", "resource_kind", "resource_id", "version_id")
        if any(not isinstance(item.get(key), str) for key in required):
            raise ValueError("resource binding is incomplete")
        bindings.append(
            {
                key: item[key]
                for key in (
                    "binding_id",
                    "resource_kind",
                    "resource_id",
                    "version_id",
                    "is_current",
                    "supersedes_binding_id",
                )
                if key in item
            }
        )
    return bindings


def _normalize_tool_calls(
    raw_calls: object,
    definitions: tuple[ToolDefinition, ...],
) -> list[dict[str, JsonValue]] | None:
    if raw_calls in (None, []):
        return []
    if not isinstance(raw_calls, list) or len(raw_calls) > _MAX_TOOL_CALLS:
        return None
    by_name = {definition.name: definition for definition in definitions}
    normalized: list[dict[str, JsonValue]] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            return None
        function = raw_call.get("function")
        if not isinstance(function, dict):
            return None
        name = function.get("name")
        if not isinstance(name, str) or name not in by_name:
            return None
        arguments = _arguments(function.get("arguments"))
        if arguments is None:
            return None
        encoded = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(encoded) > _MAX_ARGUMENT_BYTES:
            return None
        call_id = raw_call.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = hashlib.sha256(f"{index}:{name}:".encode() + encoded).hexdigest()[:32]
        definition = by_name[name]
        normalized.append(
            {
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
                "requires_approval": definition.requires_approval,
                "risk_summary": definition.risk_summary,
            }
        )
    return normalized


def _arguments(raw: object) -> dict[str, JsonValue] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    try:
        # Round-trip rejects arbitrary Python objects and normalizes JSON values.
        value = json.loads(json.dumps(raw, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


__all__ = ["ModelCallActivityHandler"]
