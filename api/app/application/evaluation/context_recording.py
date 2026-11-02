"""Exact summary replay keys with compiler-declared identity fields only."""

import json
from copy import deepcopy

from app.domain.evaluation.errors import ReplayMismatch
from app.domain.execution.context_models import context_digest
from app.domain.execution.context_summary import ContextSummary

SUMMARY_RECORDING_TOOL = "__context_compact__"


def summary_recording_contract():
    from app.domain.evaluation.recording import RecordedContract
    from app.domain.models.tool_policy import ToolExecutionPolicy

    return RecordedContract(
        name=SUMMARY_RECORDING_TOOL,
        pack="context_recording",
        schema_body={
            "type": "function",
            "function": {
                "name": SUMMARY_RECORDING_TOOL,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "request_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    },
                    "required": ["request_digest"],
                    "additionalProperties": False,
                },
            },
        },
        policy=ToolExecutionPolicy(
            capability="generation", effect="read_only", idempotency="safe", approval="never"
        ),
        binding_revision="context-recording-v1",
        authority_revision="context-recording-v1",
    )


def summary_replay_request(plan, chunk, *, round_index):
    if not chunk.groups:
        raise ReplayMismatch("context_recording_groups_missing")
    aliases = {ref.source_id: f"source:{index}" for index, ref in enumerate(chunk.sources)}
    if len(aliases) != len(chunk.sources):
        raise ReplayMismatch("context_recording_sources_ambiguous")
    messages = [deepcopy(message.wire()) for message in chunk.messages]
    calls = {}
    for group_index, group in enumerate(chunk.groups):
        label = "Source identities for the following evidence: "
        messages[group.start]["content"] = label + ", ".join(
            aliases[source] for source in group.label_sources
        )
        for index in range(group.start + 1, group.start + group.message_count):
            message = messages[index]
            if group.kind in {"knowledge", "memory", "attachment", "summary"}:
                prefix = f"Evidence ({group.kind}; source {group.source_id}):\n"
                if not message["content"].startswith(prefix):
                    raise ReplayMismatch("context_recording_group_changed")
                content = message["content"][len(prefix) :]
                if group.kind == "summary":
                    try:
                        summary = ContextSummary.model_validate_json(content)
                        content = json.dumps(
                            {
                                "facts": [
                                    {
                                        **fact.model_dump(mode="json"),
                                        "source_ids": [
                                            aliases[source] for source in fact.source_ids
                                        ],
                                    }
                                    for fact in summary.facts
                                ],
                            },
                            sort_keys=True,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    except (ValueError, KeyError) as error:
                        raise ReplayMismatch("context_recording_summary_changed") from error
                identity = aliases.get(group.source_id, f"derived:{group_index}")
                message["content"] = f"Evidence ({group.kind}; source {identity}):\n" + content
            for call in message.get("tool_calls", ()):
                identity = call["id"]
                if identity in calls:
                    raise ReplayMismatch("context_recording_call_ambiguous")
                calls[identity] = f"call:{len(calls)}"
                call["id"] = calls[identity]
            if "tool_call_id" in message:
                identity = message["tool_call_id"]
                if identity not in calls:
                    raise ReplayMismatch("context_recording_call_unmatched")
                message["tool_call_id"] = calls[identity]
    return {
        "format_version": 1,
        "definition_digest": plan.definition_pin.manifest_digest,
        "round_index": round_index,
        "ordinal": chunk.ordinal,
        "policy_digest": context_digest(plan.policy.model_dump(mode="json")),
        "candidates": [
            {
                "identity_digest": candidate.identity_digest,
                "profile_digest": candidate.profile_digest,
            }
            for candidate in plan.candidates
        ],
        "input_budget": plan.input_budget,
        "output_budget": plan.output_tokens,
        "source_kinds": [ref.kind for ref in chunk.sources],
        "messages": messages,
    }, aliases


def recorded_summary(summary, aliases):
    """Replace only typed attribution fields; never UUID-like text inside a fact."""
    try:
        return {
            "facts": [
                {
                    **fact.model_dump(mode="json"),
                    "source_ids": [aliases[source] for source in fact.source_ids],
                }
                for fact in summary.facts
            ],
        }
    except KeyError as error:
        raise ReplayMismatch("context_recording_summary_unbound") from error


def restore_summary(value, aliases, chunk):
    original = {alias: source for source, alias in aliases.items()}
    try:
        if not isinstance(value, dict) or set(value) != {"facts"}:
            raise ValueError("invalid recorded summary shape")
        summary = ContextSummary(
            chunk_digest=chunk.digest,
            facts=[
                {**fact, "source_ids": [original[source] for source in fact["source_ids"]]}
                for fact in value["facts"]
            ],
        )
        summary.require_chunk(chunk)
        return summary
    except (KeyError, TypeError, ValueError) as error:
        raise ReplayMismatch("context_recording_summary_unbound") from error
