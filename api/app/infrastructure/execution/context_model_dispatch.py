"""A model permit is bound to a formally completed prepared context."""

from sqlalchemy import text

from app.application.execution import activity_types
from app.domain.evaluation.context_profile import context_profiles
from app.domain.execution.context_models import ContextCompilation
from app.domain.execution.serialization import canonical_json_bytes
from app.infrastructure.execution.context_wire_payload import context_payload_matches


class ContextModelDispatchValidator:
    def __init__(self, objects, sources, contracts):
        self.objects, self.sources, self.contracts = objects, sources, contracts

    async def validate(self, work, state, request, model, payload, candidates):
        ref = request.input_payload.get("context_ref")
        completed = {
            activity
            for activity, generation, result_ref, _, _ in state.activity_results
            if generation == request.generation and result_ref == ref
        }
        if not completed or not any(
            activity in completed
            and kind == activity_types.CONTEXT_PREPARE
            and generation == request.generation
            for activity, kind, generation in state.requested_activities
        ):
            raise ValueError("CONTEXT_MANIFEST_UNAVAILABLE")
        actual = await work.db_session.scalar(
            text("""
SELECT request_payload FROM execution_activity_tasks WHERE activity_id=:activity
 AND aggregate_id=CAST(CAST(:run AS uuid) AS text) AND request_generation=:generation
 AND activity_type='model.call' FOR SHARE
"""),
            {
                "activity": request.activity_id,
                "run": state.run_id,
                "generation": request.generation,
            },
        )
        if actual is None or canonical_json_bytes(actual) != canonical_json_bytes(
            request.input_payload
        ):
            raise ValueError("CONTEXT_MANIFEST_MISMATCH")
        result = await self.objects.load_context_document(ref)
        manifest = ContextCompilation.model_validate(result.get("compilation")).manifest
        if (
            result.get("kind") != "context_prepare"
            or manifest is None
            or manifest.definition_pin != state.definition_pin
            or manifest.policy != state.definition_manifest.context_policy
            or manifest.digest != request.input_payload.get("context_digest")
            or manifest.boundary.current_input_digest != request.input_digest
            or manifest.boundary.current_run_id != state.run_id
            or manifest.boundary.scope_key
            != (f"team:{state.team_id}" if state.team_id else f"user:{state.owner_user_id}")
            or manifest.candidates
            != context_profiles(candidates, output_tokens=manifest.output_tokens)
        ):
            raise ValueError("CONTEXT_MANIFEST_MISMATCH")
        if not context_payload_matches(
            model.provider.value,
            payload,
            [message.wire() for message in manifest.messages],
            list(manifest.tools),
        ):
            raise ValueError("CONTEXT_MODEL_PAYLOAD_CHANGED")
        await self.sources.validate_in_uow(work, manifest.boundary, manifest.original_closure)
        await self.contracts.validate_in_uow(work, state, manifest)
        return {
            "digest": manifest.digest,
            "messages_digest": manifest.messages_digest,
            "definition_digest": state.definition_pin.manifest_digest,
        }


class PreparedContextValidation:
    """Early checks complement transaction-owned physical validation."""

    def __init__(self, sources):
        self.sources = sources

    async def validate(self, manifest, request, context, *, catalog):
        if (
            catalog is not None
            and tuple(
                definition.contract.digest if definition.contract else ""
                for definition in catalog.definitions
            )
            != manifest.tool_contract_digests
        ):
            raise ValueError("CONTEXT_TOOL_CONTRACT_CHANGED")
        await self.sources.validate(manifest.boundary, manifest.original_closure)
