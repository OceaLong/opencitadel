from uuid import uuid4

import pytest

from app.application.evaluation.context_recording import (
    recorded_summary,
    restore_summary,
    summary_recording_contract,
    summary_replay_request,
)
from app.domain.evaluation.errors import ReplayMismatch
from app.domain.execution.context_models import (
    ContextCompactionChunk,
    ContextPolicy,
    context_digest,
)
from app.domain.execution.context_summary import ContextSummary
from tests.app.application.execution.test_context_compiler import compile, source


def plan_with_prefix(prefix):
    sources = [
        source(index, "literal source:1 stays in user text; amount=7341") for index in range(1, 7)
    ]
    sources = [
        item.model_copy(
            update={
                "ref": item.ref.model_copy(
                    update={
                        "source_id": f"{prefix}:{index}",
                        "run_id": uuid4(),
                    }
                )
            }
        )
        for index, item in enumerate(sources)
    ]
    return compile(sources, policy=ContextPolicy(selected_sources=4, recent_units=1)).compaction


def test_new_run_identity_mapping_does_not_rewrite_quoted_user_content():
    assert summary_recording_contract().policy.capability.value == "generation"
    first, second = plan_with_prefix("first"), plan_with_prefix("second")
    a, aliases_a = summary_replay_request(first, first.chunks[0], round_index=0)
    b, aliases_b = summary_replay_request(second, second.chunks[0], round_index=0)
    assert first.chunks[0].digest != second.chunks[0].digest
    assert a == b
    assert "literal source:1 stays in user text; amount=7341" in str(a)
    summary = ContextSummary(
        chunk_digest=first.chunks[0].digest,
        facts=[
            {
                "kind": "constraint",
                "text": "keep literal first:0 untouched",
                "source_ids": [next(iter(aliases_a))],
            }
        ],
    )
    normalized = recorded_summary(summary, aliases_a)
    restored = restore_summary(normalized, aliases_b, second.chunks[0])
    assert restored.facts[0].text == summary.facts[0].text
    assert restored.facts[0].source_ids == (next(iter(aliases_b)),)
    assert summary_replay_request(second, second.chunks[0], round_index=1)[0] != a


def test_summary_replay_requires_typed_groups_and_exact_source_attribution():
    plan = plan_with_prefix("first")
    chunk = plan.chunks[0]
    material = {
        "sources": [ref.model_dump(mode="json") for ref in chunk.sources],
        "messages": [message.wire() for message in chunk.messages],
    }
    legacy = ContextCompactionChunk(
        ordinal=chunk.ordinal,
        sources=chunk.sources,
        messages=chunk.messages,
        digest=context_digest(material),
    )
    with pytest.raises(ReplayMismatch, match="context_recording_groups_missing"):
        summary_replay_request(plan, legacy, round_index=0)
    _, aliases = summary_replay_request(plan, chunk, round_index=0)
    with pytest.raises(ReplayMismatch, match="context_recording_summary_unbound"):
        restore_summary(
            {"facts": [{"kind": "fact", "text": "wrong owner", "source_ids": ["foreign"]}]},
            aliases,
            chunk,
        )
    with pytest.raises(ReplayMismatch, match="context_recording_summary_unbound"):
        restore_summary({"facts": [], "unexpected": True}, aliases, chunk)


def test_compiler_group_boundaries_are_part_of_chunk_digest():
    plan = plan_with_prefix("first")
    chunk = plan.chunks[0]
    body = chunk.model_dump(mode="json")
    body["groups"][0]["start"] += 1
    with pytest.raises(ValueError, match="group evidence mismatch"):
        ContextCompactionChunk.model_validate(body)
