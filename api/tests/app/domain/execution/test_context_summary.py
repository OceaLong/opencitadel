from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.execution.context_summary import (
    ContextSummary,
    ContextSummaryProposal,
    logical_summary_identity,
)
from tests.app.application.execution.test_context_compiler import PIN, compile, source


def test_summary_identity_is_stable_across_worker_claims_but_separates_chunks_and_pins():
    run = uuid4()
    first = logical_summary_identity(run, PIN, round_index=7, ordinal=2, chunk_digest="a" * 64)
    assert first == logical_summary_identity(
        run, PIN, round_index=7, ordinal=2, chunk_digest="a" * 64
    )
    assert first != logical_summary_identity(
        run, PIN, round_index=7, ordinal=3, chunk_digest="a" * 64
    )
    assert first != logical_summary_identity(
        run,
        PIN.model_copy(update={"manifest_digest": "b" * 64}),
        round_index=7,
        ordinal=2,
        chunk_digest="a" * 64,
    )


def test_model_summary_cannot_invent_source_identity_or_instruction_role():
    compiled = compile([source(i, "x" * 300) for i in range(150)])
    chunk = compiled.compaction.chunks[0]
    summary = ContextSummary(
        chunk_digest=chunk.digest,
        facts=(
            {
                "kind": "constraint",
                "text": "Do not modify the locked field",
                "source_ids": (chunk.sources[0].source_id,),
            },
        ),
    )
    summary.require_chunk(chunk)
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        ContextSummary(
            chunk_digest=chunk.digest,
            facts=({"kind": "fact", "text": "invented", "source_ids": ("unrecorded-source",)},),
        ).require_chunk(chunk)
    with pytest.raises(ValidationError, match="Extra inputs"):
        ContextSummary.model_validate(
            {
                "chunk_digest": chunk.digest,
                "facts": summary.model_dump(mode="json")["facts"],
                "role": "system",
            }
        )


def test_recovery_proposal_requires_namespace_physical_receipt_and_content_addressed_result():
    run, activity = uuid4(), uuid4()
    body = {
        "logical_id": logical_summary_identity(
            run, PIN, round_index=0, ordinal=0, chunk_digest="a" * 64
        ),
        "scope_key": "user:summary",
        "run_id": run,
        "activity_id": activity,
        "request_generation": 0,
        "definition_pin": PIN,
        "round_index": 0,
        "ordinal": 0,
        "input_digest": "b" * 64,
        "chunk_digest": "a" * 64,
        "purpose": "evaluation_subject",
        "physical_call_ids": (uuid4(),),
        "result_ref": f"execution/results/{activity}/{'c' * 64}.json",
        "result_digest": "c" * 64,
    }
    with pytest.raises(ValidationError, match="NAMESPACE_REQUIRED"):
        ContextSummaryProposal(**body)
    value = ContextSummaryProposal(**body, namespace_id=uuid4())
    assert value.logical_id == body["logical_id"]
    with pytest.raises(ValidationError):
        ContextSummaryProposal(**{**body, "physical_call_ids": ()}, namespace_id=uuid4())
