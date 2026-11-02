"""Exact summary lineage and recovery binding shared by handler and dispatch."""

from app.application.execution import activity_types
from app.application.execution.decisions.base import activity_identity, settled_status
from app.application.execution.decisions.context import context_enabled
from app.domain.execution.context_summary import (
    ContextCompactBinding,
    logical_summary_identity,
    summary_activity_key,
)
from app.domain.execution.run import RunFamily, RunStatus


def require_summary_lineage(state, request, *, root_run_id=None):
    binding = ContextCompactBinding.model_validate(request.input_payload)
    manifest = state.definition_manifest
    maximum_rounds = (
        state.policy_snapshot.family_policy.agent.max_iterations + int(context_enabled(manifest))
        if state.family == RunFamily.AGENT and state.policy_snapshot is not None
        else (
            3
            if state.source_entity_type == "evaluation_judge"
            and state.semantic_payload.get("judge_protocol") == 1
            else 1
        )
    )
    if (
        state.status != RunStatus.RUNNING
        or state.definition_pin is None
        or manifest is None
        or manifest.context_policy is None
        or request.definition_digest != manifest.digest
        or request.handler_version != manifest.handler_version(activity_types.CONTEXT_COMPACT)
        or request.generation != state.retry_generation
        or (request.activity_id, activity_types.CONTEXT_COMPACT, request.generation)
        not in state.requested_activities
        or activity_identity(state, summary_activity_key(binding)) != request.activity_id
        or (binding.prepare_activity_id, activity_types.CONTEXT_PREPARE, request.generation)
        not in state.requested_activities
        or settled_status(state, binding.prepare_activity_id) != "succeeded"
        or not any(
            identity == binding.prepare_activity_id
            and generation == request.generation
            and ref == binding.prepare_ref
            for identity, generation, ref, _, _ in state.activity_results
        )
        or binding.ordinal >= manifest.context_policy.compactions_per_run
        or binding.round_index >= maximum_rounds
        or sum(kind == activity_types.CONTEXT_COMPACT for _, kind, _ in state.requested_activities)
        > manifest.context_policy.compactions_per_run
    ):
        raise ValueError("CONTEXT_SUMMARY_LINEAGE_UNAVAILABLE")
    return logical_summary_identity(
        root_run_id or state.run_id,
        state.definition_pin,
        round_index=binding.round_index,
        ordinal=binding.ordinal,
        chunk_digest=binding.chunk_digest,
    )


def require_proposal_binding(proposal, request, run):
    binding = ContextCompactBinding.model_validate(request.input_payload)
    scope = run.owner_scope
    scope_key = f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}"
    if (
        proposal.scope_key != scope_key
        or proposal.run_id != run.run_id
        or proposal.activity_id != request.activity_id
        or proposal.request_generation != request.generation
        or proposal.definition_pin != run.definition_pin
        or proposal.input_digest != request.input_digest
        or proposal.chunk_digest != binding.chunk_digest
        or proposal.round_index != binding.round_index
        or proposal.ordinal != binding.ordinal
    ):
        raise ValueError("CONTEXT_SUMMARY_PROPOSAL_MISMATCH")
    return proposal
