"""Build atomic protocol units from verified, bounded Activity messages."""

from dataclasses import dataclass

from app.domain.execution.context_models import (
    ContextMessage,
    ContextSource,
    ContextSourceRef,
    context_digest,
)


@dataclass(frozen=True)
class ActivityContextEvidence:
    ref: ContextSourceRef
    message: ContextMessage
    dependencies: tuple[ContextSourceRef, ...] = ()


def protocol_sources(evidence):
    """A model call and all its results remain one indivisible compiler input."""
    results = {}
    for item in evidence:
        if item.message.role == "tool":
            identity = item.message.tool_call_id
            if identity in results:
                raise ValueError("CONTEXT_SOURCE_PROTOCOL_INVALID")
            results[identity] = item
    consumed, sources = set(), []
    for item in evidence:
        if item.message.role == "tool":
            continue
        messages, closure = [item.message], list(item.dependencies)
        for call in item.message.tool_calls:
            result = results.get(call["id"])
            if result is None or call["id"] in consumed:
                raise ValueError("CONTEXT_SOURCE_PROTOCOL_INVALID")
            consumed.add(call["id"])
            messages.append(result.message)
            closure.extend((result.ref, *result.dependencies))
        by_id = {}
        for ref in closure:
            if by_id.setdefault(ref.source_id, ref) != ref:
                raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
        sources.append(
            ContextSource(
                ref=item.ref,
                messages=tuple(messages),
                messages_digest=context_digest(
                    [message.model_dump(mode="json") for message in messages]
                ),
                original_closure=tuple(by_id.values()),
            )
        )
    if set(results) != consumed:
        raise ValueError("CONTEXT_SOURCE_PROTOCOL_INVALID")
    return tuple(sources)
