"""One durable, budgeted summary chunk; persisted responses survive worker loss."""

import json
import re
from uuid import UUID

from pydantic import ValidationError

from app.application.evaluation.budget_candidates import BudgetPayloadGuard
from app.application.execution import activity_types
from app.application.execution.context_compaction import require_proposal_binding
from app.application.ports.inference_dispatch import (
    candidate_authority_context,
    dispatch_context,
    owned_inference_adapter,
)
from app.domain.evaluation.context_profile import context_profiles
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.execution.activity import ActivityOutcome
from app.domain.execution.context_models import ContextCompilation
from app.domain.execution.context_summary import (
    ContextCompactBinding,
    ContextSummary,
    ContextSummaryProposal,
    logical_summary_identity,
)
from app.domain.execution.definition import DefinitionIntegrityError, DefinitionUnavailable
from app.domain.execution.serialization import canonical_json_bytes


class ContextCompactActivityHandler:
    activity_type = activity_types.CONTEXT_COMPACT
    idempotent = False

    def __init__(
        self,
        *,
        objects,
        definitions,
        proposals,
        sources,
        usage,
        models,
        client_factory,
        replay=None,
    ):
        self.objects, self.definitions, self.proposals = objects, definitions, proposals
        self.sources, self.usage, self.models = sources, usage, models
        self.client_factory = client_factory
        self.replay = replay

    async def recovery_safe(self, request, run):
        if self.replay is not None and await self.replay.recovery_safe(request, run):
            return True
        # Never retry an unqueryable provider invocation solely because its
        # content-addressed object happens to exist without a fenced receipt.
        return await self.proposals.load(request, run) is not None

    async def execute(self, request, context):
        try:
            return await self._execute(request, context)
        except ReplayMismatch:
            return ActivityOutcome.failed(failure_code=ReplayMismatch.code)
        except ValueError as error:
            code = str(error)
            if re.fullmatch(r"CONTEXT_[A-Z_]{1,80}", code):
                return ActivityOutcome(
                    status="failed",
                    failure_code=code,
                    public_data={"kind": "context", "context": {"stage": "compact"}},
                )
            raise

    async def _execute(self, request, context):
        pin = context.run.definition_pin
        if pin is None:
            raise DefinitionUnavailable("summary requires an admitted definition")
        manifest = self.definitions.resolve(pin).manifest
        if (
            manifest != context.run.definition_manifest
            or manifest.context_policy is None
            or request.definition_digest != manifest.digest
            or request.handler_version != manifest.handler_version(self.activity_type)
        ):
            raise DefinitionIntegrityError("summary binding differs from the admitted definition")
        binding = ContextCompactBinding.model_validate(request.input_payload)
        prepared = await self.objects.load_context_document(binding.prepare_ref)
        compilation = ContextCompilation.model_validate(prepared.get("compilation"))
        plan = compilation.compaction
        scope = context.run.owner_scope
        scope_key = f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"
        if (
            prepared.get("kind") != "context_prepare"
            or plan is None
            or plan.definition_pin != pin
            or plan.policy != manifest.context_policy
            or plan.digest != binding.compaction_digest
            or plan.boundary.current_input_digest != request.input_digest
            or plan.boundary.scope_key != scope_key
        ):
            raise ValueError("CONTEXT_SUMMARY_PREPARATION_MISMATCH")
        chunk = next((item for item in plan.chunks if item.ordinal == binding.ordinal), None)
        if chunk is None or chunk.digest != binding.chunk_digest:
            raise ValueError("CONTEXT_SUMMARY_PREPARATION_MISMATCH")
        await self.sources.validate(plan.boundary, chunk.sources)
        proposal = await self.proposals.load(request, context.run)
        if proposal is not None:
            require_proposal_binding(proposal, request, context.run)
            result = await self.objects.load_result_bounded(proposal.result_ref)
            if result.get("kind") != "context_compact":
                raise ValueError("CONTEXT_SUMMARY_PROPOSAL_MISMATCH")
            summary = ContextSummary.model_validate(result.get("summary"))
            summary.require_chunk(chunk)
            return self._outcome(proposal.result_ref, summary, plan=plan, chunk=chunk)
        candidates, purpose, namespace_id = await self.usage.summary_authority(
            context.run.owner_scope, context
        )
        if purpose not in manifest.summary_purposes:
            raise ValueError("CONTEXT_SUMMARY_PURPOSE_UNAVAILABLE")
        if context_profiles(candidates, output_tokens=plan.output_tokens) != plan.candidates:
            raise ValueError("CONTEXT_SUMMARY_CANDIDATES_CHANGED")
        if self.replay is not None and await self.replay.active(context):
            if purpose != "evaluation_subject" or namespace_id is None:
                raise ReplayMismatch("context_recording_namespace_missing")
            summary = await self.replay.summary(
                request, context, plan, chunk, round_index=binding.round_index
            )
            await self.sources.validate(plan.boundary, chunk.sources)
            ref = await self.objects.put_result(
                request.activity_id,
                {
                    "kind": "context_compact",
                    "summary": summary.model_dump(mode="json"),
                },
                run_id=context.run.run_id,
            )
            return self._outcome(ref, summary, plan=plan, chunk=chunk)
        scope = context.run.owner_scope
        model = candidates.primary(
            await self.models.resolve_chat(
                candidates.proof["candidates"][0]["identity"]["model_id"],
                scope=scope,
            )
        )
        logical = logical_summary_identity(
            context.run.run_id,
            pin,
            round_index=binding.round_index,
            ordinal=binding.ordinal,
            chunk_digest=chunk.digest,
        )
        messages = [message.wire() for message in chunk.messages]
        downstream = self.usage.guard(
            scope=scope,
            request=request,
            context=context,
            purpose=purpose,
            resolved={
                "messages": messages,
                "tools": [],
                "thinking_enabled": False,
                "policy_revision": str(context.run.policy_snapshot.execution_revision_id),
                "tool_fingerprint": "tools-disabled",
                "context_summary": {
                    "logical_id": str(logical),
                    "chunk_digest": chunk.digest,
                    "namespace_id": str(namespace_id) if namespace_id else None,
                },
            },
        )
        receipts = _SourceCheckedSummaryGuard(
            BudgetPayloadGuard(candidates, downstream),
            self.sources,
            plan.boundary,
            chunk.sources,
        )
        client = self.client_factory(
            model,
            policy=context.run.policy_snapshot.common.model_resilience,
            thinking_enabled=False,
            inference_model_service=self.models,
            scope=scope,
        )
        async with owned_inference_adapter(client):
            with candidate_authority_context(candidates), dispatch_context(receipts, model):
                try:
                    response = await client.invoke(messages, tools=None)
                except Exception as error:
                    # Some provider adapters wrap pre-send guard errors as a
                    # transport exception. A rejected permit with zero accepted
                    # sends has a known failure; never turn it into unknown.
                    if receipts.rejection_code is not None and not receipts.started:
                        raise ValueError(receipts.rejection_code) from error
                    raise
        if not receipts.completed:
            raise ValueError("CONTEXT_SUMMARY_PHYSICAL_RECEIPT_UNAVAILABLE")
        if response.get("tool_calls") or response.get("_raw_tool_calls"):
            raise ValueError("CONTEXT_SUMMARY_FORMAT_INVALID")
        text = response.get("content")
        if not isinstance(text, str) or len(text.encode()) > 262144:
            raise ValueError("CONTEXT_SUMMARY_FORMAT_INVALID")
        try:
            body = json.loads(text)
            # The digest belongs to the kernel, not a model's assertion.
            if not isinstance(body, dict) or set(body) != {"facts"}:
                raise ValueError("CONTEXT_SUMMARY_FORMAT_INVALID")
            summary = ContextSummary(chunk_digest=chunk.digest, facts=body["facts"])
            summary.require_chunk(chunk)
        except (json.JSONDecodeError, ValidationError) as error:
            raise ValueError("CONTEXT_SUMMARY_FORMAT_INVALID") from error
        result = {"kind": "context_compact", "summary": summary.model_dump(mode="json")}
        await self.sources.validate(plan.boundary, chunk.sources)
        if len(canonical_json_bytes(result)) > 262144:
            raise ValueError("CONTEXT_SUMMARY_FORMAT_INVALID")
        ref = await self.objects.put_result(request.activity_id, result, run_id=context.run.run_id)
        proposal = ContextSummaryProposal(
            logical_id=logical,
            scope_key=plan.boundary.scope_key,
            run_id=context.run.run_id,
            activity_id=request.activity_id,
            request_generation=request.generation,
            definition_pin=pin,
            round_index=binding.round_index,
            ordinal=binding.ordinal,
            input_digest=request.input_digest,
            chunk_digest=chunk.digest,
            purpose=purpose,
            namespace_id=namespace_id,
            physical_call_ids=tuple(receipts.completed),
            result_ref=ref,
            result_digest=ref.rsplit("/", 1)[-1].removesuffix(".json"),
        )
        await self.proposals.save(proposal, request, context)
        return self._outcome(ref, summary, plan=plan, chunk=chunk)

    @staticmethod
    def _outcome(ref, summary, *, plan, chunk):
        return ActivityOutcome.succeeded(
            result_ref=ref,
            result_summary="context compacted",
            decision_data={"context_status": "compacted", "chunk_digest": summary.chunk_digest},
            public_data={
                "kind": "context",
                "context": {
                    "stage": "compact",
                    "state": "compacted",
                    "definition_version": plan.definition_pin.definition_version,
                    "definition_digest": plan.definition_pin.manifest_digest,
                    "context_digest": summary.chunk_digest,
                    "original_sources": len(chunk.sources),
                    "input_budget": plan.input_budget,
                    "output_budget": plan.output_tokens,
                    "summary_chunks": 1,
                },
            },
        )


class _SourceCheckedSummaryGuard:
    def __init__(self, downstream, sources, boundary, refs):
        self.downstream, self.sources, self.boundary, self.refs = (
            downstream,
            sources,
            boundary,
            refs,
        )
        self.started = set()
        self.completed = []
        self.rejection_code = None

    async def before_send(self, model, payload):
        if payload.get("tools") or payload.get("functions"):
            raise ValueError("CONTEXT_SUMMARY_TOOLS_FORBIDDEN")
        try:
            await self.sources.validate(self.boundary, self.refs)
            permit = await self.downstream.before_send(model, payload)
        except PermissionError:
            self.rejection_code = "CONTEXT_SOURCE_AUTHORIZATION_REVOKED"
            raise
        except ValueError as error:
            code = str(error)
            if re.fullmatch(r"CONTEXT_[A-Z_]{1,80}", code):
                self.rejection_code = code
            elif code == "budget_exhausted":
                self.rejection_code = "CONTEXT_SUMMARY_BUDGET_EXCEEDED"
            raise
        identity = permit.call_identity if hasattr(permit, "call_identity") else permit
        self.started.add(UUID(identity))
        return permit

    async def after_send(self, identity, usage, revision):
        key = UUID(identity)
        if key not in self.started:
            raise ValueError("CONTEXT_SUMMARY_PHYSICAL_RECEIPT_MISMATCH")
        await self.downstream.after_send(identity, usage, revision)
        if key not in self.completed:
            self.completed.append(key)

    async def after_completion(self, identity, revision):
        return await self.downstream.after_completion(identity, revision)
