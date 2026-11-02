"""Revalidate the formal preparation and sources inside physical admission."""

from sqlalchemy import text

from app.application.execution.context_compaction import require_summary_lineage
from app.domain.evaluation.context_profile import context_profiles
from app.domain.execution.context_models import ContextCompilation
from app.domain.execution.context_summary import ContextCompactBinding, logical_summary_identity
from app.domain.execution.serialization import canonical_json_bytes
from app.infrastructure.execution.context_wire_payload import context_payload_matches


class ContextSummaryDispatchValidator:
    def __init__(self, objects, sources):
        self.objects, self.sources = objects, sources

    async def validate(
        self, work, state, request, payload, candidates, *, purpose, namespace_id, model=None
    ):
        require_summary_lineage(state, request)
        binding = ContextCompactBinding.model_validate(request.input_payload)
        row = (
            (
                await work.db_session.execute(
                    text("""
SELECT request_payload,request_digest,request_ref,definition_digest,handler_version
 FROM execution_activity_tasks WHERE activity_id=:activity AND aggregate_id=CAST(CAST(:run AS uuid) AS text)
 AND request_generation=:generation AND activity_type='context.compact' FOR SHARE
"""),
                    {
                        "activity": request.activity_id,
                        "run": state.run_id,
                        "generation": request.generation,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or (
            canonical_json_bytes(row["request_payload"])
            != canonical_json_bytes(request.input_payload)
            or row["request_digest"] != request.input_digest
            or row["request_ref"] != request.input_ref
            or row["definition_digest"] != request.definition_digest
            or row["handler_version"] != request.handler_version
        ):
            raise ValueError("CONTEXT_SUMMARY_LINEAGE_UNAVAILABLE")
        prepared = await self.objects.load_context_document(binding.prepare_ref)
        plan = ContextCompilation.model_validate(prepared.get("compilation")).compaction
        if (
            prepared.get("kind") != "context_prepare"
            or plan is None
            or plan.definition_pin != state.definition_pin
            or plan.policy != state.definition_manifest.context_policy
            or plan.digest != binding.compaction_digest
            or plan.boundary.current_input_digest != request.input_digest
            or plan.boundary.current_run_id != state.run_id
            or plan.candidates != context_profiles(candidates, output_tokens=plan.output_tokens)
            or purpose not in state.definition_manifest.summary_purposes
            or len(plan.chunks) > plan.policy.compactions_per_round
            or plan.boundary.scope_key
            != (f"team:{state.team_id}" if state.team_id else f"user:{state.owner_user_id}")
        ):
            raise ValueError("CONTEXT_SUMMARY_PREPARATION_MISMATCH")
        chunk = next((item for item in plan.chunks if item.ordinal == binding.ordinal), None)
        if (
            chunk is None
            or chunk.digest != binding.chunk_digest
            or not context_payload_matches(
                model.provider.value if model is not None else "openai",
                payload,
                [message.wire() for message in chunk.messages],
                [],
            )
        ):
            raise ValueError("CONTEXT_SUMMARY_PAYLOAD_CHANGED")
        # The implementation must lock live authority until this same UoW commits.
        await self.sources.validate_in_uow(work, plan.boundary, chunk.sources)
        logical = logical_summary_identity(
            state.run_id,
            state.definition_pin,
            round_index=binding.round_index,
            ordinal=binding.ordinal,
            chunk_digest=chunk.digest,
        )
        return {
            "logical_id": str(logical),
            "chunk_digest": chunk.digest,
            "namespace_id": str(namespace_id) if namespace_id else None,
        }


async def validate_candidate_metadata(work, scope, candidates):
    from app.domain.evaluation.configuration import ConfigSelection

    for candidate in candidates.proof["candidates"]:
        metadata = await work.evaluation_configuration.metadata(
            scope,
            ConfigSelection(model_id=candidate["identity"]["model_id"]),
        )
        if (
            metadata["identity"] != candidate["identity"]
            or metadata["settings"] != candidate["base_settings"]
            or metadata["capabilities"] != candidate["capabilities"]
            or metadata["extra_params_present"]
            or not metadata["credential_configured"]
            or (
                "price" in candidate
                and candidate["price"]
                != {key: metadata["price"].get(key) for key in ("input", "output")}
            )
        ):
            raise ValueError("budget_candidate_changed")
