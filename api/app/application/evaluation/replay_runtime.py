"""Trusted replay dispatch before real catalog construction, memory recall or invocation."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from app.application.evaluation.replay_adapter import ReplayAdapter
from app.application.execution.tool_catalog import CatalogSnapshot, ToolDefinition
from app.domain.evaluation.errors import ReplayMismatch


class ReplayRuntime:
    def __init__(self, authority, objects, source_factory):
        self.authority, self.objects, self.source_factory = authority, objects, source_factory
        self.adapter = ReplayAdapter(authority, objects)

    async def active(self, context):
        binding = await self.authority.binding(context.run)
        if (
            binding is None
            and getattr(context.run, "source_entity_type", None) == "evaluation_recorded_case"
        ):
            raise ReplayMismatch("replay_binding_missing")
        return binding is not None

    async def recovery_safe(self, request, run):
        # A revoked binding must fail replay, never become an unknown real write.
        binding = await self.authority.binding(run)
        if (
            binding is None
            and getattr(run, "source_entity_type", None) == "evaluation_recorded_case"
        ):
            raise ReplayMismatch("replay_binding_missing")
        return binding is not None

    async def definitions(self, context):
        async with self.authority.open(context) as access:
            return CatalogSnapshot(
                definitions=tuple(
                    ToolDefinition(
                        name=c.name,
                        tool_schema=c.schema_body,
                        requires_approval=c.policy.requires_approval(),
                        risk_summary=f"{c.policy.effect.value}: {c.name}",
                        approval_kind=c.policy.approval_kind,
                        approval_prompt_param=c.policy.approval_prompt_param,
                        approval_choices_param=c.policy.approval_choices_param,
                    )
                    for c in access.manifest.contracts
                    if c.name in access.admission["tool_names"]
                ),
                fingerprint=access.manifest.catalog_fingerprint,
            )

    async def tool(self, request, context):
        call = request.input_payload["tool_call"]
        async with self.authority.open(context) as access:
            if call["name"] not in access.admission["tool_names"]:
                raise ReplayMismatch("tool_not_selected")
            candidates = [c for c in access.manifest.contracts if c.name == call["name"]]
            if len(candidates) != 1:
                raise ReplayMismatch("tool_unmatched")
            contract = candidates[0]
            # Gate before locating the current parent model and before all recording bodies.
            await self.authority.approve(access, context, contract.policy)
            scope, authorization = access.scope, access.authorization
        source = self.source_factory(authorization)
        page = await source.views.list_steps(
            scope, context.run.run_id, filters={"activity_id": str(request.activity_id)}, limit=2
        )
        if len(page.items) != 1 or not page.items[0].parent_step_id:
            raise ReplayMismatch("call_parent_unavailable")
        cut = await source.views.get_step_cut(
            scope, context.run.run_id, page.items[0].parent_step_id
        )
        output, redacted, _ = await source.complete(
            scope, context.run.run_id, cut.step.step_id, cut.at, "output"
        )
        if redacted:
            raise ReplayMismatch("call_parent_redacted")
        calls = output.get("message", {}).get("tool_calls", [])
        positions = [
            i
            for i, item in enumerate(calls)
            if item.get("id") == call["call_id"]
            and item.get("function", {}).get("name") == call["name"]
        ]
        round_index = request.input_payload.get("round")
        if len(positions) != 1 or type(round_index) is not int:
            raise ReplayMismatch("call_order_unavailable")
        expected = uuid5(
            NAMESPACE_URL,
            f"opencitadel:{context.run.run_id}:activity:{request.generation}:tool:{round_index}:{positions[0]}:{call['call_id']}",
        )
        parent_expected = uuid5(
            NAMESPACE_URL,
            f"opencitadel:{context.run.run_id}:activity:{request.generation}:model:{round_index}",
        )
        if request.activity_id != expected or cut.step.activity_id != parent_expected:
            raise ReplayMismatch("call_identity_unverified")
        arguments = dict(call["arguments"])
        feedback = request.input_payload.get("approval_feedback")
        if feedback and contract.policy.approval_feedback_param:
            arguments[contract.policy.approval_feedback_param] = feedback
        result = await self.adapter.match(
            context,
            contract.name,
            contract.digest,
            arguments,
            "root",
            positions[0],
            parallel_group="round:" + str(round_index),
        )
        return await self._read(context, contract, result)

    async def retrieval(self, request, context, query):
        from app.application.evaluation.recording_worker import retrieval_contract

        contract = retrieval_contract()
        result = await self.adapter.match(
            context,
            contract.name,
            contract.digest,
            {"query": query},
            "root",
            0,
            parallel_group="retrieval",
        )
        return await self._read(context, contract, result)

    async def _read(self, context, contract, result):
        async with self.authority.open(context) as access:
            await self.authority.approve(access, context, contract.policy)
            previous = await access.repo.consumed(
                access.scope, context.run.run_id, context.activity_id
            )
            if previous is None:
                raise ReplayMismatch("replay_claim_missing")
            slot = next(s for s in access.manifest.slots if s.id == previous["slot_id"])
            obj = await access.repo.object(access.scope, slot.object_id)
            if obj["storage_key"] != result.result_ref:
                raise ReplayMismatch("replay_result_changed")
            try:
                data = await self.objects.get_bytes(result.result_ref)
            except (KeyError, FileNotFoundError) as error:
                raise ReplayMismatch("recorded_object_missing") from error
            if (
                len(data) != slot.result_bytes
                or hashlib.sha256(data).hexdigest() != slot.result_digest
            ):
                raise ReplayMismatch("recorded_object_changed")
            metadata = slot.citation_evidence
            if contract.name == "__retrieval__" and metadata is not None:
                if not slot.source_step_id or not any(
                    pin.resource_kind == "execution_content"
                    and pin.resource_id == str(metadata.output_content_id)
                    and pin.resource_version == metadata.output_digest
                    for pin in access.manifest.pins
                ):
                    raise ReplayMismatch("recorded_citations_unbound")
                if context.record_citations is not None:
                    # These references originate from the fixed authorized source
                    # cut, not the replaceable recorded result or answer text.
                    await context.record_citations(list(metadata.citations))
            return json.loads(data)

    async def coverage(self, context):
        from app.domain.evaluation.recording import ReplayCoverage

        async with self.authority.open(context) as access:
            row = await access.repo.coverage(access.scope, context.run.run_id)
            return ReplayCoverage(
                total=row["total"],
                consumed=row["consumed"],
                unused=row["total"] - row["consumed"],
                mismatches=row["mismatches"],
                quality_eligible=row["mismatches"] == 0,
            )
