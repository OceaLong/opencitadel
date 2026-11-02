"""Durable, inference-free context preparation for an exact code definition."""

import re

from app.application.execution import activity_types
from app.application.execution.context_io import ContextReadBudget
from app.domain.execution.activity import ActivityOutcome
from app.domain.execution.context_models import ContextCompilation
from app.domain.execution.definition import DefinitionIntegrityError, DefinitionUnavailable


class ContextPrepareActivityHandler:
    activity_type = activity_types.CONTEXT_PREPARE
    idempotent = True

    def __init__(self, *, objects, definitions, preparation):
        self.objects = objects
        self.definitions = definitions
        self.preparation = preparation

    async def execute(self, request, context):
        try:
            definition = context.run.definition_manifest
            if definition is None or definition.context_policy is None:
                raise DefinitionUnavailable("context preparation requires a frozen policy")
            with ContextReadBudget(definition.context_policy.prepare_bytes):
                return await self._execute(request, context)
        except ValueError as error:
            code = str(error)
            if re.fullmatch(r"CONTEXT_[A-Z_]{1,80}", code):
                return ActivityOutcome(
                    status="failed",
                    failure_code=code,
                    public_data={"kind": "context", "context": {"stage": "prepare"}},
                )
            raise

    async def _execute(self, request, context):
        pin = context.run.definition_pin
        if pin is None:
            raise DefinitionUnavailable("context preparation requires an admitted definition")
        registered = self.definitions.resolve(pin)
        manifest = registered.manifest
        if manifest.context_policy is None:
            raise DefinitionUnavailable("definition does not declare a frozen context policy")
        if manifest != context.run.definition_manifest:
            raise DefinitionIntegrityError("context preparation manifest differs from the Run")
        version = manifest.handler_version(self.activity_type)
        if request.definition_digest != manifest.digest or request.handler_version != version:
            raise DefinitionIntegrityError(
                "context preparation Activity binding differs from the definition"
            )
        if request.input_ref is None:
            return ActivityOutcome.failed(failure_code="ACTIVITY_INPUT_MISSING")
        payload = await self.objects.load_input_bounded(
            key=request.input_ref,
            expected_digest=request.input_digest,
            limit=manifest.context_policy.read_bytes,
        )
        inputs = await self.preparation.materialize(request, context, payload, definition=manifest)
        scope = context.run.owner_scope
        scope_key = f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"
        if (
            inputs.pin != pin
            or inputs.policy != manifest.context_policy
            or inputs.boundary.scope_key != scope_key
            or inputs.boundary.current_input_digest != request.input_digest
        ):
            raise DefinitionIntegrityError(
                "prepared context is not bound to the admitted Run input"
            )
        compilation = ContextCompilation.model_validate(
            registered.compiler(
                pin=inputs.pin,
                boundary=inputs.boundary,
                policy=inputs.policy,
                candidates=inputs.candidates,
                summary_candidates=inputs.summary_candidates,
                sources=inputs.sources,
                tools=inputs.tools,
                tool_contract_digests=inputs.tool_contract_digests,
                tool_contracts=inputs.tool_contracts,
                compactions_used=inputs.compactions_used,
                unavailable_sources=inputs.unavailable_sources,
            )
        )
        if compilation.status == "failed":
            return ActivityOutcome.failed(failure_code=compilation.failure_code)
        result = {
            "kind": "context_prepare",
            "compilation": compilation.model_dump(mode="json"),
            "original_lookups": list(inputs.original_lookups),
        }
        ref = await self.objects.put_context_document(
            request.activity_id, result, run_id=context.run.run_id
        )
        value = compilation.manifest if compilation.manifest is not None else compilation.compaction
        data = {"context_status": compilation.status, "context_digest": value.digest}
        if compilation.compaction is not None:
            data["compaction_chunks"] = [
                {"ordinal": chunk.ordinal, "digest": chunk.digest}
                for chunk in compilation.compaction.chunks
            ]
        return ActivityOutcome.succeeded(
            result_ref=ref,
            result_summary=compilation.status,
            decision_data=data,
            public_data={
                "kind": "context",
                "context": {
                    "stage": "prepare",
                    "state": compilation.status,
                    "definition_version": pin.definition_version,
                    "definition_digest": pin.manifest_digest,
                    "context_digest": value.digest,
                    "selected_sources": len(compilation.manifest.sources)
                    if compilation.manifest is not None
                    else None,
                    "original_sources": len(compilation.manifest.original_closure)
                    if compilation.manifest is not None
                    else len({ref.source_id for chunk in value.chunks for ref in chunk.sources}),
                    "estimated_input_units": compilation.manifest.estimated_tokens
                    if compilation.manifest is not None
                    else None,
                    "input_budget": value.input_budget,
                    "output_budget": value.output_tokens,
                    "summary_chunks": len(compilation.compaction.chunks)
                    if compilation.compaction is not None
                    else 0,
                },
            },
        )
