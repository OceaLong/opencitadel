"""Attach replay material from the claimed compact Activity's verified prepare object."""

from app.application.evaluation.context_recording import recorded_summary, summary_replay_request
from app.application.execution import activity_types
from app.domain.execution.context_models import ContextCompilation, context_digest
from app.domain.execution.context_summary import ContextCompactBinding, ContextSummary
from app.domain.models.resource_pin import ResourceUnavailable


async def summary_capture_evidence(task, value, objects):
    if task.activity_type != activity_types.CONTEXT_COMPACT:
        return None
    binding = ContextCompactBinding.model_validate(task.request_payload)
    prepared = await objects.load_context_document(binding.prepare_ref)
    plan = ContextCompilation.model_validate(prepared.get("compilation")).compaction
    if plan is None or plan.digest != binding.compaction_digest:
        raise ResourceUnavailable("summary capture preparation differs")
    chunk = next(
        (
            item
            for item in plan.chunks
            if item.ordinal == binding.ordinal and item.digest == binding.chunk_digest
        ),
        None,
    )
    if chunk is None or value.get("kind") != "context_compact":
        raise ResourceUnavailable("summary capture result differs")
    summary = ContextSummary.model_validate(value.get("summary"))
    summary.require_chunk(chunk)
    request, aliases = summary_replay_request(plan, chunk, round_index=binding.round_index)
    return {
        "format_version": 1,
        "request": request,
        "request_digest": context_digest(request),
        "result": recorded_summary(summary, aliases),
        "prepare_activity_id": str(binding.prepare_activity_id),
        "compaction_digest": binding.compaction_digest,
        "chunk_digest": binding.chunk_digest,
    }
