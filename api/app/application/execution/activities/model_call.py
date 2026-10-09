"""Durable LLM Activity that emits either a final answer or governed tool intent."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

from app.application.evaluation.budget_candidates import BudgetPayloadGuard
from app.application.execution import activity_types
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.tool_catalog import ExecutionToolCatalog, ToolDefinition
from app.application.ports.inference_dispatch import (
    candidate_authority_context,
    dispatch_context,
    owned_inference_adapter,
)
from app.application.services.execution_usage_service import ExecutionUsageService
from app.application.services.file_service import FileService
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.skill_service import SkillService
from app.domain.errors import TooManyRequestsError
from app.domain.execution.activity import (
    ActivityContext,
    ActivityOutcome,
    ActivityRequest,
)
from app.domain.execution.commands import JsonValue
from app.domain.external.llm import LLM
from app.domain.models.scope import OwnerScope

if TYPE_CHECKING:
    from app.application.services.quota_service import QuotaService
from app.domain.services.skills.skill_loader import render_active
from app.domain.services.vision_service import (
    build_user_message,
    prepare_media_attachments_from_files,
)

logger = logging.getLogger(__name__)

_MAX_TOOL_CALLS = 16
_MAX_ARGUMENT_BYTES = 64 * 1024


class ModelCallActivityHandler:
    activity_type = activity_types.MODEL_CALL
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
        execution_usage: ExecutionUsageService | None = None,
        files: FileService | None = None,
        quota: QuotaService | None = None,
        client_factory: Callable[..., LLM],
        judge=None,
        text_stream: bool = False,
    ) -> None:
        self._text_stream = text_stream
        self._judge = judge
        self._objects = objects
        self._models = models
        self._tools = tools
        self._skills = skills
        self._token_usage = token_usage
        self._execution_usage = execution_usage
        self._files = files
        # 运行中 Token 预算拦截依赖；未注入时退化为无操作。
        self._quota = quota
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
        restricted = context.run.source_entity_type == "evaluation_judge"
        judge = None
        if restricted:
            if self._judge is None:
                return ActivityOutcome.failed(failure_code="JUDGE_AUTHORITY_UNAVAILABLE")
            judge = await self._judge.authorize(_owner_scope(context), request, context)
            # Durable typed authority supplies all provider inputs. Ignore ambient
            # payload settings, attachments, sessions, skills, and history.
            payload = {
                "message": prompt,
                "model_id": judge["model_id"],
                "temperature_override": judge["temperature"],
                "_execution_usage": {"purpose": "evaluation_judge"},
            }
        history = [] if restricted else await self._load_history(request)
        if history is None:
            return ActivityOutcome.failed(failure_code="MODEL_HISTORY_INVALID")
        scope = _owner_scope(context)
        model_id = payload.get("model_id")
        if model_id is not None and not isinstance(model_id, str):
            return ActivityOutcome.failed(failure_code="MODEL_ID_INVALID")
        candidates = (
            await self._execution_usage.candidates(scope, context)
            if self._execution_usage
            else None
        )
        if candidates is not None:
            primary_id = candidates.proof["candidates"][0]["identity"]["model_id"]
            if model_id is not None and model_id != primary_id:
                raise ValueError("budget_candidate_changed")
            model_id = primary_id
        model = await self._models.resolve_chat(model_id, scope=scope)
        if candidates is not None:
            model = candidates.primary(model)
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
        if candidates is not None:
            candidates.validate(model, candidates.proof["candidates"][0], effective=True)
        client = self._client_factory(
            model,
            policy=context.run.policy_snapshot.common.model_resilience,
            thinking_enabled=payload.get("thinking_enabled") is True,
            inference_model_service=self._models,
            scope=scope,
        )
        # The factory returns a fresh adapter owned by this activity.
        async with owned_inference_adapter(client):
            # 运行中月度 Token 预算复查：会话创建时只查一次，长会话可能建成后持续超额，
            # 故在每次模型调用准入处按已用量对用户 / 团队配额再校验一次。
            if self._quota is not None:
                try:
                    await self._quota.check_model_call_budget(
                        user_id=context.owner_user_id,
                        team_id=context.team_id,
                    )
                except TooManyRequestsError:
                    return ActivityOutcome.failed(failure_code="MODEL_CALL_BUDGET_EXCEEDED")
            allow_tools = not restricted and request.input_payload.get("allow_tools") is True
            snapshot = await self._tools.definitions(payload, context) if allow_tools else None
            if not allow_tools and not restricted:
                capture_disabled = getattr(self._tools, "capture_disabled", None)
                if capture_disabled is not None:
                    await capture_disabled(context)
            definitions = snapshot.definitions if snapshot is not None else ()
            schemas = [definition.tool_schema for definition in definitions]
            resolved_skill = {}
            if restricted:
                from app.domain.evaluation.judge_protocol import SYSTEM_PROMPT

                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(judge["materials"], ensure_ascii=False)},
                ]
                skill_disabled = False
            else:
                messages, skill_disabled = await self._messages(
                    payload=payload,
                    prompt=prompt,
                    history=history,
                    scope=scope,
                    client=client,
                    snapshot_sink=resolved_skill if self._execution_usage is not None else None,
                )
            marker = payload.get("_execution_usage")
            purpose = marker.get("purpose", "unknown") if isinstance(marker, dict) else "unknown"
            if purpose not in {"production", "evaluation_subject", "evaluation_judge", "unknown"}:
                raise ValueError("invalid admitted usage purpose")
            guard = (
                self._execution_usage.guard(
                    scope=scope,
                    request=request,
                    context=context,
                    purpose=purpose,
                    resolved={
                        "admission_configuration_id": marker.get("configuration_id")
                        if isinstance(marker, dict)
                        else None,
                        "messages": messages,
                        "tools": schemas,
                        "skill": resolved_skill,
                        "knowledge_bindings": payload.get("resource_bindings", []),
                        "thinking_enabled": payload.get("thinking_enabled") is True,
                        "tool_fingerprint": snapshot.fingerprint
                        if snapshot is not None
                        else "tools-disabled",
                        "policy_revision": str(context.run.policy_snapshot.execution_revision_id),
                    },
                )
                if self._execution_usage is not None
                else None
            )
            if candidates is not None:
                guard = BudgetPayloadGuard(candidates, guard)
            with (
                candidate_authority_context(candidates)
                if candidates is not None
                else nullcontext(),
                dispatch_context(guard, model),
            ):
                if (
                    self._text_stream
                    and model.provider.value in {"openai", "azure", "ollama"}
                    and callable(getattr(client, "stream_invoke", None))
                    and context.run.family.value == "ask"
                    and context.run.source_entity_type == "session"
                    and not allow_tools
                    and purpose in {"production", "unknown"}
                ):
                    response = await self._stream_response(client, messages, context)
                else:
                    response = await client.invoke(messages, tools=schemas or None)
            await self._record_usage(
                request=request,
                context=context,
                payload=payload,
                response=response,
                client=client,
                fallback_model=model,
            )
            await self._report_usage_progress(context=context, response=response)
            content = response.get("content")
            if not isinstance(content, str):
                content = "" if content is None else str(content)
            if restricted and (
                response.get("tool_calls") not in (None, []) or response.get("_raw_tool_calls")
            ):
                return ActivityOutcome.failed(failure_code="JUDGE_TOOL_CALL_FORBIDDEN")
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
            decision_data: dict[str, JsonValue] = {"tool_calls": normalized}
            if restricted:
                from app.domain.evaluation.judge_protocol import validate_output

                try:
                    validate_output(content, judge["materials"])
                    judge_status = "valid"
                except ValueError:
                    judge_status = "invalid"
                decision_data = {
                    "judge_protocol": 1,
                    "judge_round": request.input_payload["round"],
                    "judge_status": judge_status,
                }
            if snapshot is not None:
                # 目录快照摘要（D9）：只落工具名单与指纹，避免撑大 decision payload。
                decision_data["catalog"] = {
                    "tool_names": list(snapshot.tool_names),
                    "fingerprint": snapshot.fingerprint,
                }
            public_data: dict[str, JsonValue] = {
                "kind": "message",
                "role": "assistant",
                "message": content[:65_536],
                "resource_bindings": _public_bindings(payload),
            }
            if skill_disabled:
                # Skill 被禁用时降级为"无 skill 继续"（P2-10）；通过 public_data
                # 提示而非通知链，保持实现最小。
                public_data["skill_disabled"] = True
            return ActivityOutcome.succeeded(
                result_ref=result_ref,
                result_summary=content[:4096],
                decision_data=decision_data,
                public_data=public_data,
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
        snapshot_sink: dict | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        mode = str(payload.get("mode") or "agent")
        from app.application.execution.system_prompt import platform_system_prompt

        system = platform_system_prompt(mode)
        skill_disabled = False
        skill_id = payload.get("skill_id")
        if self._skills is not None and isinstance(skill_id, str) and skill_id:
            skill = await self._skills.get_skill(skill_id, scope=scope)
            if snapshot_sink is not None:
                snapshot_sink.update(
                    {
                        "id": skill.id,
                        "revision": skill.updated_at.isoformat(),
                        "enabled": skill.enabled,
                        "rendered": render_active(skill) if skill.enabled else None,
                    }
                )
            if skill.enabled:
                system = f"{system}\n\n{render_active(skill)}"
            else:
                # 与 agent_tool_catalog 的降级语义一致（P2-10）：无 skill 继续。
                skill_disabled = True
                logger.warning(
                    "skill %s is disabled; model call continues without it",
                    skill_id,
                )
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
        ], skill_disabled

    async def _report_usage_progress(
        self,
        *,
        context: ActivityContext,
        response: dict[str, Any],
    ) -> None:
        """Actual response-phase completion; never an aggregate terminal fact."""
        if context.report_progress is not None:
            await context.report_progress(
                {
                    "kind": "step",
                    "phase": "model_response",
                    "status": "completed",
                    "progress": 100,
                    "message": "Model response complete",
                }
            )

    async def _stream_response(self, client, messages, context):
        fragments, size, finish, usage = [], 0, None, None
        # Close the adapter generator synchronously on consumer rejection/cancel.
        # The transport owns physical evidence; never synthesize settlement here.
        async with owned_inference_adapter(client.stream_invoke(messages, tools=None)) as stream:
            async for chunk in stream:
                if (
                    not isinstance(chunk, dict)
                    or chunk.get("tool_calls")
                    or chunk.get("_raw_tool_calls")
                ):
                    raise ValueError("text stream contains unsupported output")
                content = chunk.get("content")
                if content is not None and not isinstance(content, str):
                    raise ValueError("text stream content is invalid")
                if content:
                    if finish is not None:
                        raise ValueError("text stream content follows terminal")
                    size += len(content.encode("utf-8"))
                    if size > 1024 * 1024:
                        raise ValueError("text stream exceeds bounded output")
                    fragments.append(content)
                    if context.report_progress is not None:
                        # Zero means no percentage estimate; counts are actual
                        # received nonempty fragments, never tokens/raw output.
                        await context.report_progress(
                            {
                                "kind": "step",
                                "phase": "model_response",
                                "status": None,
                                "progress": 0,
                                "message": f"Received fragments: {len(fragments)}",
                            }
                        )
                if chunk.get("finish_reason") is not None:
                    if finish is not None or chunk["finish_reason"] != "stop":
                        raise ValueError("text stream did not finish normally")
                    finish = chunk["finish_reason"]
                if "usage" in chunk:
                    if not isinstance(chunk["usage"], dict) or usage is not None:
                        raise ValueError("text stream usage is invalid")
                    usage = chunk["usage"]
        if finish != "stop":
            raise ValueError("text stream has no terminal evidence")
        response = {"content": "".join(fragments), "tool_calls": []}
        if usage is not None:
            response["_usage"] = usage
        return response

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
        entry: dict[str, JsonValue] = {
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
            "requires_approval": definition.requires_approval,
            "risk_summary": definition.risk_summary,
            "approval_kind": definition.approval_kind,
        }
        # Declarative approval-card metadata (clarification tools): the card's
        # prompt text and selectable options come from the tool's own
        # arguments, per the parameters the tool declared — never keyed on
        # tool names.
        if definition.approval_prompt_param:
            prompt = arguments.get(definition.approval_prompt_param)
            if isinstance(prompt, str) and prompt.strip():
                entry["risk_summary"] = prompt.strip()[:1024]
        if definition.approval_choices_param:
            raw_choices = arguments.get(definition.approval_choices_param)
            choices = [
                choice.strip()[:200]
                for choice in (raw_choices if isinstance(raw_choices, list) else [])
                if isinstance(choice, str) and choice.strip()
            ]
            if choices:
                entry["approval_choices"] = choices[:6]
        normalized.append(entry)
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
