"""Lease-fenced recording generation. Reads finish before publication transaction."""

import hashlib
import json
from uuid import uuid4

from app.application.evaluation.dataset_service import fingerprint
from app.application.execution.content_sanitization import sanitize_content
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.evaluation.recording import (
    MAX_RECORDING_TOTAL_BYTES,
    RecordedContract,
    RecordingCitationEvidence,
    RecordingManifest,
    RecordingSelection,
    RecordingSlot,
    canonical,
    recording_key,
    sanitize_result,
)
from app.domain.execution.context_resources import ContextResourceEvidence
from app.domain.models.audit_log import AuditLog
from app.domain.models.knowledge_citation import KnowledgeCitation, deduplicate_citations
from app.domain.models.resource_pin import ResourceIdentity
from app.domain.models.tool_policy import ToolEffect
from app.domain.models.tool_result import ToolResult


class RecordingWorker:
    def __init__(self, service, object_lifecycle):
        self.service, self.lifecycle = service, object_lifecycle

    async def generate(self, scope, principal, job_id):
        service = self.service
        auth = service.auth(scope, principal)
        async with service.uow_factory(auth) as uow:
            await uow.evaluation_dataset.authorize(scope, principal, write=True)
            token = await uow.evaluation_recording.claim(scope, job_id)
            row = await uow.evaluation_recording.job(scope, job_id)
            await uow.commit()
        if token is None:
            return
        try:
            manifest = await self._generate(scope, principal, row, token)
            request_id = "recording-publish:" + str(job_id)
            async with service.uow_factory(service.auth(scope, principal, request_id)) as uow:
                await uow.evaluation_dataset.authorize(scope, principal, write=True)
                await uow.evaluation_recording.publish(scope, manifest, token)
                await uow.resource_pins.acquire(
                    scope, "recording_version", str(manifest.id), manifest.pins
                )
                from app.application.evaluation.recording_authority import validate_recording

                await validate_recording(uow, scope, principal, manifest.id)
                result = {
                    "id": str(job_id),
                    "revision": 1,
                    "result_version": str(manifest.id),
                    "operation": "recording.publish",
                    "audit_resource_id": str(job_id),
                }
                await uow.evaluation_dataset.save_receipt(
                    scope, request_id, fingerprint("recording.publish", [str(job_id)]), result
                )
                await uow.audit.add_evaluation(
                    AuditLog(
                        actor_user_id=principal.user_id,
                        team_id=scope.team_id,
                        action="evaluation.recording.publish",
                        resource_type="evaluation_recording",
                        resource_id=str(job_id),
                        request_id=request_id,
                        metadata={"revision": 1},
                    ),
                    authorization=service.auth(scope, principal, request_id),
                )
                await uow.evaluation_dataset.authorize(scope, principal, write=True)
                await uow.commit()
        except (ValueError, PermissionError) as error:
            async with service.uow_factory(auth) as uow:
                await uow.evaluation_recording.fail(
                    scope,
                    job_id,
                    token,
                    error.reason
                    if isinstance(error, ReplayMismatch)
                    else "recording_generation_invalid",
                )
                await uow.commit()
        # Crashes/transport errors keep a reclaimable lease, never ready partial output.

    async def _generate(self, scope, principal, job, token):
        source, run_id = self.service.source, job["source_run_id"]
        at, steps = await source.steps(scope, run_id)
        if any(step.activity_id is not None and step.status != "completed" for step in steps):
            raise ReplayMismatch("source_activity_incomplete")
        selections = {s.tool: s for s in map(RecordingSelection.model_validate, job["selection"])}
        from app.application.evaluation.context_recording import SUMMARY_RECORDING_TOOL

        if SUMMARY_RECORDING_TOOL in selections:
            raise ReplayMismatch("context_recording_selection_reserved")
        evidence, pins, contracts, slots, catalog = {}, {}, {}, [], None
        citation_evidence = {}
        total_bytes = 0
        for step in steps:
            if step.activity_id is None or step.status != "completed":
                continue
            inp, input_redacted, input_id = await source.complete(
                scope, run_id, step.step_id, at, "input"
            )
            out, output_redacted, output_id = await source.complete(
                scope, run_id, step.step_id, at, "output"
            )
            total_bytes += len(canonical(inp)) + len(canonical(out))
            if total_bytes > MAX_RECORDING_TOTAL_BYTES:
                raise ReplayMismatch("recording_total_size_limit")
            evidence[step.step_id] = (step, inp, out, input_redacted, output_redacted)
            async with self.service.uow_factory(self.service.auth(scope, principal)) as uow:
                await uow.evaluation_dataset.authorize(scope, principal, write=False)
                for identity in (input_id, output_id):
                    digest = await uow.evaluation_recording.content_identity(
                        scope, run_id, identity
                    )
                    pins[str(identity)] = ResourceIdentity(
                        resource_kind="execution_content",
                        resource_id=str(identity),
                        resource_version=digest,
                    )
            if out.get("kind") == "retrieval":
                citations = []
                for ref in getattr(step, "citation_refs", None) or ():
                    if ref.resource_kind != "knowledge_base":
                        continue
                    # F06 authorizes these separately captured canonical refs. A
                    # sanitized public body does not invalidate its provenance;
                    # selected result fields still must pass redaction checks below.
                    if ref.availability != "available" or not ref.knowledge_base_id:
                        raise ReplayMismatch("source_citations_unavailable")
                    citation = KnowledgeCitation.model_validate(
                        {key: getattr(ref, key) for key in KnowledgeCitation.model_fields}
                    )
                    citations.append(citation)
                if citations:
                    citation_evidence[step.step_id] = RecordingCitationEvidence(
                        source_at=at,
                        output_content_id=output_id,
                        output_digest=pins[str(output_id)].resource_version,
                        citations=tuple(deduplicate_citations(citations)),
                    )
        disabled_catalog = hashlib.sha256(canonical({"tools": [], "available": False})).hexdigest()
        saw_disabled = False
        for step, _inp, out, _input_redacted, _output_redacted in evidence.values():
            kind = out.get("kind")
            if kind == "model":
                async with self.service.uow_factory(self.service.auth(scope, principal)) as uow:
                    captured = await uow.evaluation_recording.captured(
                        scope, run_id, step.activity_id
                    )
                if not captured["contracts"] and captured["fingerprint"] == disabled_catalog:
                    saw_disabled = True
                    continue
                if catalog is not None and catalog != captured["fingerprint"]:
                    raise ReplayMismatch("source_catalog_changed")
                catalog = captured["fingerprint"]
                for raw in captured["contracts"]:
                    contract = RecordedContract.model_validate(raw)
                    if contract.name not in selections:
                        continue
                    if contract.name in contracts and contracts[contract.name] != contract:
                        raise ReplayMismatch("source_contract_changed")
                    contracts[contract.name] = contract
        if catalog is None and saw_disabled:
            catalog = disabled_catalog
        for step, inp, out, _input_redacted, output_redacted in evidence.values():
            if out.get("kind") != "context_compact":
                continue
            contract, slot = await self._summary_slot(
                scope, principal, job, token, step, inp, out, output_redacted, evidence
            )
            contracts[contract.name] = contract
            slots.append(slot)
        for step, inp, out, input_redacted, _output_redacted in evidence.values():
            kind = out.get("kind")
            if kind not in {"tool", "retrieval"}:
                continue
            if kind == "retrieval":
                name, branch, group, ordinal = "__retrieval__", "root", "retrieval", 0
                args = {"query": inp["context"]["message"]}
                result = json.loads(out["message"]["content"])
                contract = retrieval_contract()
                contracts[name] = contract
                schema = {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "sources": {"type": "array"}},
                    "required": ["query", "sources"],
                    "additionalProperties": False,
                }
            else:
                call = inp["request"]["tool_call"]
                name, args = call["name"], dict(call["arguments"])
                if step.parent_step_id not in evidence:
                    raise ReplayMismatch("source_parent_unavailable")
                _parent, parent_input, parent_output, _, parent_redacted = evidence[
                    step.parent_step_id
                ]
                if parent_redacted:
                    raise ReplayMismatch("source_parent_redacted")
                calls = parent_output.get("message", {}).get("tool_calls", [])
                positions = [
                    i
                    for i, candidate in enumerate(calls)
                    if candidate.get("id") == call["call_id"]
                    and candidate.get("function", {}).get("name") == name
                ]
                if len(positions) != 1 or type(parent_input["request"].get("round")) is not int:
                    raise ReplayMismatch("source_order_unavailable")
                branch, group, ordinal = (
                    "root",
                    "round:" + str(parent_input["request"]["round"]),
                    positions[0],
                )
                contract = contracts.get(name)
                if contract is None:
                    raise ReplayMismatch("contract_unavailable")
                feedback = inp["request"].get("approval_feedback")
                if feedback and contract.policy.approval_feedback_param:
                    args[contract.policy.approval_feedback_param] = feedback
                result = json.loads(out["message"]["content"])
                schema = ToolResult.model_json_schema()
            selection = selections.get(name)
            if selection is None:
                raise ReplayMismatch("recording_selection_incomplete")
            if step.step_id in citation_evidence and "sources" in selection.replacements:
                raise ReplayMismatch("recording_citations_replacement_forbidden")
            resources = (
                ContextResourceEvidence.model_validate(out["resource_evidence"])
                if "resource_evidence" in out
                else None
            )
            if resources is not None and "sources" in selection.replacements:
                raise ReplayMismatch("recording_sources_replacement_forbidden")
            if input_redacted and not selection.argument_replacements:
                raise ReplayMismatch("recording_arguments_redacted")
            if (
                not selection.argument_replacements.keys()
                <= contract.arguments_schema.get("properties", {}).keys()
            ):
                raise ReplayMismatch("replacement_field_invalid")
            args.update(selection.argument_replacements)
            if "[redacted]" in canonical(args).decode():
                raise ReplayMismatch("recording_arguments_redacted")
            normalized = selection.rule.normalize(args, contract.arguments_schema)
            clean = sanitize_result(
                result, schema, selection.allowed_fields, selection.replacements
            )
            if sanitize_content(clean) != clean or "[redacted]" in canonical(clean).decode():
                raise ReplayMismatch("replacement_requires_redaction")
            if contract.policy.effect != ToolEffect.READ_ONLY:
                clean["simulated_effect"] = True
                clean["recording_revision"] = 1
            object_id, digest, size = await self.lifecycle.put(
                self.service.auth(scope, principal),
                job_id=job["id"],
                claim_token=token,
                body=canonical(clean),
            )
            slots.append(
                RecordingSlot(
                    id=uuid4(),
                    tool=name,
                    contract_digest=contract.digest,
                    match_key=recording_key(
                        name,
                        contract.digest,
                        normalized,
                        branch,
                        ordinal,
                        parallel_group=group,
                        rule_version=selection.rule.version,
                    ),
                    rule=selection.rule,
                    branch=branch,
                    parallel_group=group,
                    ordinal=ordinal,
                    source_step_id=step.step_id,
                    source_parent_step_id=step.parent_step_id,
                    object_id=object_id,
                    result_digest=digest,
                    result_bytes=size,
                    simulated_effect=contract.policy.effect != ToolEffect.READ_ONLY,
                    citation_evidence=citation_evidence.get(step.step_id),
                    resource_evidence=resources,
                )
            )
        if catalog is None or not any(slot.tool == "__retrieval__" for slot in slots):
            raise ReplayMismatch("recording_incomplete")
        return RecordingManifest(
            id=uuid4(),
            job_id=job["id"],
            source_run_id=run_id,
            catalog_fingerprint=catalog,
            contracts=tuple(contracts.values()),
            slots=tuple(slots),
            pins=tuple(pins.values()),
        )

    async def _summary_slot(self, scope, principal, job, token, step, inp, out, redacted, evidence):
        from app.application.evaluation.context_recording import summary_recording_contract
        from app.application.execution.native_request_evidence import reconstruct_native_request
        from app.domain.evaluation.recording import MatchRule
        from app.domain.execution.context_models import context_digest

        data, physical = out.get("_context_replay"), out.get("_context_dispatches")
        if redacted or not isinstance(data, dict) or not isinstance(physical, dict):
            raise ReplayMismatch("context_recording_evidence_unavailable")
        request = data.get("request")
        binding = inp.get("request", {})
        prepares = [
            item
            for item in evidence.values()
            if str(item[0].activity_id) == data.get("prepare_activity_id")
        ]
        if (
            not isinstance(request, dict)
            or context_digest(request) != data.get("request_digest")
            or data.get("chunk_digest") != binding.get("chunk_digest")
            or data.get("compaction_digest") != binding.get("compaction_digest")
            or request.get("ordinal") != binding.get("ordinal")
            or request.get("round_index") != binding.get("round_index")
            or len(prepares) != 1
            or prepares[0][2].get("kind") != "context_prepare"
        ):
            raise ReplayMismatch("context_recording_binding_changed")
        calls = physical.get("calls")
        if not isinstance(calls, list) or not calls:
            raise ReplayMismatch("context_recording_physical_unavailable")
        for call in calls:
            if (
                not call.get("native_payload_exact")
                or reconstruct_native_request(call["request_snapshot"]) is None
                or (call.get("settlement") or {}).get("call_identity") != call.get("call_identity")
                or call.get("configuration", {}).get("context_summary", {}).get("chunk_digest")
                != data["chunk_digest"]
            ):
                raise ReplayMismatch("context_recording_physical_inexact")
        contract = summary_recording_contract()
        branch, group, ordinal = (
            "root",
            f"context:round:{request['round_index']}",
            request["ordinal"],
        )
        args = {"request_digest": data["request_digest"]}
        body = canonical(
            {
                "summary": data["result"],
                "request": request,
                "physical": physical,
                "compaction_digest": data["compaction_digest"],
                "chunk_digest": data["chunk_digest"],
            }
        )
        object_id, digest, size = await self.lifecycle.put(
            self.service.auth(scope, principal),
            job_id=job["id"],
            claim_token=token,
            body=body,
        )
        return contract, RecordingSlot(
            id=uuid4(),
            tool=contract.name,
            contract_digest=contract.digest,
            match_key=recording_key(
                contract.name, contract.digest, args, branch, ordinal, parallel_group=group
            ),
            rule=MatchRule(),
            branch=branch,
            parallel_group=group,
            ordinal=ordinal,
            source_step_id=step.step_id,
            source_parent_step_id=prepares[0][0].step_id,
            object_id=object_id,
            result_digest=digest,
            result_bytes=size,
            simulated_effect=False,
        )


def retrieval_contract():
    from app.domain.models.tool_policy import ToolExecutionPolicy

    return RecordedContract(
        name="__retrieval__",
        pack="retrieval",
        schema_body={
            "type": "function",
            "function": {
                "name": "__retrieval__",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        policy=ToolExecutionPolicy(
            capability="knowledge_read", effect="read_only", idempotency="safe", approval="never"
        ),
        binding_revision="retrieval-v1",
        authority_revision="retrieval-v1",
    )
