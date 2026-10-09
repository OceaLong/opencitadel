"""Capture resolved inputs before send; never consult mutable settings on reads."""

from app.application.execution.content_sanitization import sanitize_content
from app.domain.models.execution_usage import PriceSnapshot


def configuration_snapshot(
    resolved_model,
    *,
    messages=(),
    tools=(),
    policy_revision,
    tool_fingerprint,
    skill=None,
    knowledge_bindings=(),
    thinking_enabled=False,
    price_snapshot=None,
    admission_configuration_id=None,
):
    model = resolved_model
    price = price_snapshot or PriceSnapshot(
        input_per_million=str(model.model.input_price_per_million)
        if model.model.input_price_per_million > 0
        else None,
        output_per_million=str(model.model.output_price_per_million)
        if model.model.output_price_per_million > 0
        else None,
        provenance="legacy_positive",
    )
    clean_messages = sanitize_content(list(messages))
    clean_tools = sanitize_content(list(tools))
    return {
        "admission_configuration_id": admission_configuration_id,
        "model_id": model.id,
        "configured_model": model.model_name,
        "provider": model.provider.value,
        "endpoint_id": model.endpoint.id,
        "credential_ref": {"kind": "inference_endpoint", "id": model.endpoint.id},
        "settings": model.model.settings.model_dump(mode="json"),
        "thinking_enabled": thinking_enabled,
        "extra_parameters": {"availability": "omitted", "reason": "not_allowlisted"}
        if model.extra_params
        else {},
        "prompt": {
            "messages": clean_messages,
            "redacted": clean_messages != list(messages),
            "template_revision": "model-call-system-v1",
        },
        "tools": {
            "definitions": clean_tools,
            "fingerprint": tool_fingerprint,
            "redacted": clean_tools != list(tools),
        },
        "skill": sanitize_content(skill),
        "knowledge_bindings": sanitize_content(list(knowledge_bindings)),
        "policy_revision": str(policy_revision),
        "version_unpinned": True,
        "price": price.model_dump(mode="json"),
        "price_revision": price.revision,
    }


_REQUEST_FIELDS = frozenset(
    {
        "model",
        "messages",
        "system",
        "contents",
        "tools",
        "tool_choice",
        "response_format",
        "temperature",
        "max_tokens",
        "max_completion_tokens",
        "top_p",
        "top_k",
        "seed",
        "generationConfig",
        "stream",
        "stream_options",
        "parallel_tool_calls",
        "thinking",
        "reasoning_effort",
        "stop",
    }
)


def request_snapshot(payload):
    allowed = {key: value for key, value in payload.items() if key in _REQUEST_FIELDS}
    clean = sanitize_content(allowed)
    controls = {
        key: payload[key]
        for key in ("temperature", "max_tokens", "max_completion_tokens", "top_p", "top_k", "seed")
        if type(payload.get(key)) in (int, float)
    }
    for container, fields in (
        ("generationConfig", ("temperature", "maxOutputTokens", "topP", "topK")),
        ("thinking", ("budget_tokens",)),
    ):
        if isinstance(payload.get(container), dict):
            controls[container] = {
                key: payload[container][key]
                for key in fields
                if type(payload[container].get(key)) in (int, float)
            }
    return {
        "request": clean,
        "inference": controls,
        "redacted": clean != payload,
        "omitted_field_count": len(payload) - len(allowed),
    }


class ExecutionUsageService:
    def __init__(self, *, repository_context, physical_dispatch=None, admission_authorization=None):
        self.repositories = repository_context
        self.physical_dispatch = physical_dispatch
        self.admission_authorization = admission_authorization

    def admission_resolver(self, models):
        async def resolve(
            scope, run_id, policy_revision, payload, purpose, *, inference_read_context=None
        ):
            if not isinstance(payload.get("message"), str):
                if self.physical_dispatch is None:
                    return None
                body = {
                    "policy_revision": str(policy_revision),
                    "configuration_kind": "requester_only",
                }
            else:
                model = await models.resolve_chat(
                    payload.get("model_id"),
                    scope=scope,
                    **(
                        {"uow": inference_read_context}
                        if inference_read_context is not None
                        else {}
                    ),
                )
                temperature = payload.get("temperature_override")
                if type(temperature) in (int, float) and 0 <= temperature <= 2:
                    model = model.model_copy(
                        update={
                            "model": model.model.model_copy(
                                update={
                                    "settings": model.model.settings.model_copy(
                                        update={"temperature": float(temperature)}
                                    )
                                }
                            )
                        }
                    )
                body = configuration_snapshot(
                    model,
                    policy_revision=policy_revision,
                    tool_fingerprint=None,
                    thinking_enabled=payload.get("thinking_enabled", False),
                )
            body["stage"] = "admission"

            async def capture(repository):
                if self.physical_dispatch is not None:
                    from app.application.security.authorization_context import (
                        get_authorization_context,
                    )

                    authorization = self.admission_authorization or get_authorization_context()
                    if inference_read_context is not None:
                        authorization = (
                            inference_read_context.authorization_context or authorization
                        )
                    body["physical_requester"] = await repository.capture_requester(
                        scope, authorization, run_id=run_id
                    )
                return await repository.admission_snapshot(scope, run_id, body, purpose)

            if inference_read_context is not None:
                return await capture(inference_read_context.execution_usage)
            async with self.repositories() as repository:
                return await capture(repository)

        return resolve

    async def snapshot_run_configuration(
        self,
        scope,
        run_id,
        resolved_model,
        tool_fingerprint,
        policy_revision,
        *,
        purpose="production",
        **resolved,
    ) -> str:
        body = configuration_snapshot(
            resolved_model,
            tool_fingerprint=tool_fingerprint,
            policy_revision=policy_revision,
            **resolved,
        )
        async with self.repositories() as repository:
            return await repository.snapshot(scope, run_id, body, purpose)

    async def record(self, scope, fact):
        # A repeat callback must supply the same immutable evidence, not a new clock.
        async with self.repositories() as repository:
            return await repository.record(scope, fact["call_identity"], fact)

    async def candidates(self, scope, context):
        if self.physical_dispatch is None:
            return None
        return await self.physical_dispatch.candidates(scope, context)

    def guard(self, *, scope, request, context, purpose, resolved):
        return UsageDispatchGuard(self, scope, request, context, purpose, resolved)


class UsageDispatchGuard:
    def __init__(self, service, scope, request, context, purpose, resolved):
        self.service, self.scope, self.request, self.context = service, scope, request, context
        self.purpose, self.resolved = purpose, resolved
        self.prices = {}

    async def before_send(self, model, payload):
        if self.service.physical_dispatch is not None:
            return await self.service.physical_dispatch.before_send(
                self.scope, self.request, self.context, model, payload, resolved=self.resolved
            )
        body = configuration_snapshot(model, **self.resolved)
        requested_model = payload.get("model", model.model_name)
        matches_configured = requested_model == model.model_name
        body["requested_model"] = requested_model
        body["requested_model_matches_configured"] = matches_configured
        if not matches_configured:
            unknown_price = PriceSnapshot()
            body["price"] = unknown_price.model_dump(mode="json")
            body["price_revision"] = unknown_price.revision
        snapshot = request_snapshot(payload)
        async with self.service.repositories() as repository:
            admitted_id = self.resolved.get("admission_configuration_id")
            if admitted_id is not None:
                admitted = await repository.load_snapshot(
                    self.scope, self.context.run.run_id, admitted_id, self.purpose
                )
                # Admission rates belong to this complete provider identity,
                # not merely to a mutable internal model record.
                if matches_configured and (
                    admitted.get("model_id") == model.id
                    and admitted.get("configured_model") == requested_model
                    and admitted.get("provider") == model.provider.value
                    and admitted.get("endpoint_id") == model.endpoint.id
                ):
                    price = PriceSnapshot.model_validate(admitted["price"])
                    body["price"] = price.model_dump(mode="json")
                    body["price_revision"] = price.revision
            config_id = await repository.snapshot(
                self.scope, self.context.run.run_id, body, self.purpose
            )
            identity = await repository.allocate(
                self.scope,
                run_id=self.context.run.run_id,
                activity_id=self.request.activity_id,
                generation=self.request.generation,
                claim_generation=self.context.claim_generation,
                configuration_id=config_id,
                request_snapshot=snapshot,
            )
        self.prices[identity] = (
            PriceSnapshot.model_validate(body["price"]),
            config_id,
            requested_model,
        )
        return identity

    async def after_completion(self, identity, revision):
        if self.service.physical_dispatch is not None:
            # Completion proves remote occupancy ended, not that early chunk
            # billing fields are a complete final usage statement.
            return await self.service.physical_dispatch.after_completion(
                self.scope, identity, revision
            )
        return None

    async def after_send(self, identity, usage, revision):
        if self.service.physical_dispatch is not None:
            return await self.service.physical_dispatch.after_send(
                self.scope, identity, usage, revision
            )
        price, config_id, configured_name = self.prices[identity]
        cost = price.cost(usage)
        fact = {
            "call_identity": identity,
            "usage": usage,
            "model_revision": revision,
            "version_unpinned": revision is None or revision == configured_name,
            "price_revision": price.revision,
            "configuration_id": config_id,
            "cost_usd": str(cost) if cost is not None else None,
        }
        await self.service.record(self.scope, fact)
        return None
