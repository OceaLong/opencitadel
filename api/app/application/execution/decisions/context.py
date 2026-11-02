"""Pure prepare → compact → prepare gate before a pinned model round."""

from dataclasses import dataclass

from app.application.execution import activity_types
from app.application.execution.decisions.base import (
    activity_identity,
    activity_result,
    command,
    fail_for_activity,
    request_activity,
    settled_status,
)
from app.domain.execution.context_summary import ContextCompactBinding, summary_activity_key
from app.domain.execution.definition import DefinitionUnavailable


@dataclass(frozen=True)
class ContextGate:
    pending: bool
    command: object = None
    result_ref: str | None = None
    digest: str | None = None


def context_enabled(manifest):
    return manifest is not None and any(
        item.activity_type == activity_types.CONTEXT_PREPARE for item in manifest.handlers
    )


def context_gate(state, context, *, outcomes, round_index, allow_tools, now):
    manifest = context.definition_manifest
    if not context_enabled(manifest):
        return ContextGate(pending=False)
    if manifest.context_policy is None:
        raise DefinitionUnavailable("definition requires a frozen context policy")
    limit = manifest.context_policy.compactions_per_round
    timeout = context.policy_snapshot.common.activity.tool_timeout_seconds
    used = 0
    for cycle in range(limit + 1):
        prepare_id = activity_identity(state, f"context:prepare:{round_index}:{cycle}")
        status = settled_status(state, prepare_id)
        if status is None:
            return ContextGate(
                pending=True,
                command=request_activity(
                    state,
                    activity_id=prepare_id,
                    activity_type=activity_types.CONTEXT_PREPARE,
                    now=now,
                    timeout_seconds=timeout,
                    input_ref=state.semantic_payload["input_ref"],
                    input_digest=state.semantic_payload["input_digest"],
                    input_payload={
                        "round": round_index,
                        "cycle": cycle,
                        "allow_tools": allow_tools,
                    },
                ),
            )
        if status != "succeeded":
            return ContextGate(
                pending=True,
                command=fail_for_activity(state, status, activity_id=prepare_id, max_retries=0),
            )
        result = activity_result(state, prepare_id, outcomes=outcomes)
        if result is None or result[0] is None:
            return _failed(state, "CONTEXT_PREPARATION_RESULT_MISSING")
        ref, _, data = result
        digest = data.get("context_digest")
        if not isinstance(digest, str) or len(digest) != 64:
            return _failed(state, "CONTEXT_PREPARATION_RESULT_INVALID")
        if data.get("context_status") == "ready":
            return ContextGate(pending=False, result_ref=ref, digest=digest)
        chunks = data.get("compaction_chunks")
        if (
            data.get("context_status") != "needs_compaction"
            or not isinstance(chunks, list)
            or not chunks
        ):
            return _failed(state, "CONTEXT_PREPARATION_RESULT_INVALID")
        used += len(chunks)
        if used > limit:
            return _failed(state, "CONTEXT_COMPACTION_LIMIT")
        for chunk in chunks:
            try:
                binding = ContextCompactBinding(
                    prepare_activity_id=prepare_id,
                    prepare_ref=ref,
                    compaction_digest=digest,
                    chunk_digest=chunk["digest"],
                    ordinal=chunk["ordinal"],
                    round_index=round_index,
                )
            except (ValueError, TypeError, KeyError):
                return _failed(state, "CONTEXT_PREPARATION_RESULT_INVALID")
            identity = activity_identity(state, summary_activity_key(binding))
            compact_status = settled_status(state, identity)
            if compact_status is None:
                return ContextGate(
                    pending=True,
                    command=request_activity(
                        state,
                        activity_id=identity,
                        activity_type=activity_types.CONTEXT_COMPACT,
                        now=now,
                        timeout_seconds=timeout,
                        input_ref=state.semantic_payload["input_ref"],
                        input_digest=state.semantic_payload["input_digest"],
                        input_payload=binding.model_dump(mode="json"),
                    ),
                )
            if compact_status != "succeeded":
                return ContextGate(
                    pending=True,
                    command=fail_for_activity(
                        state, compact_status, activity_id=identity, max_retries=0
                    ),
                )
    return _failed(state, "CONTEXT_COMPACTION_LIMIT")


def _failed(state, code):
    return ContextGate(
        pending=True, command=command(state, "FailRun", {"failure_code": code, "retryable": False})
    )
