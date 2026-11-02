from uuid import uuid4

import pytest

from app.application.execution.context_evidence import ActivityContextEvidence, protocol_sources
from app.domain.execution.context_models import ContextMessage
from tests.app.application.execution.test_context_compiler import compile, source


def evidence(position, message):
    ref = source(position, "identity").ref.model_copy(
        update={
            "source_id": f"activity:{uuid4()}:0",
            "formal_position": 0,
            "local_sequence": position,
            "kind": "tool" if message.role == "tool" else "assistant",
        }
    )
    return ActivityContextEvidence(ref, message)


def unit():
    calls = tuple(
        {
            "id": f"call-{i}",
            "type": "function",
            "function": {
                "name": "lookup",
                "arguments": '{"exact":"中文🙂"}',
            },
        }
        for i in range(2)
    )
    return (
        evidence(1, ContextMessage(role="assistant", content="", tool_calls=calls)),
        evidence(2, ContextMessage(role="tool", tool_call_id="call-1", content="second")),
        evidence(3, ContextMessage(role="tool", tool_call_id="call-0", content="first")),
    )


def test_parallel_tool_results_are_rejoined_without_changing_arguments_or_call_order():
    inputs = unit()
    sources = protocol_sources(inputs)
    assert len(sources) == 1
    assert [m.content for m in sources[0].messages] == ["", "first", "second"]
    assert sources[0].messages[0].tool_calls == inputs[0].message.tool_calls
    assert {ref.source_id for ref in sources[0].original_closure} == {
        inputs[1].ref.source_id,
        inputs[2].ref.source_id,
    }


@pytest.mark.parametrize("mutation", ["missing", "orphan", "duplicate"])
def test_incomplete_or_duplicate_tool_protocol_cannot_enter_a_context(mutation):
    inputs = unit()
    inputs = (
        inputs[:-1]
        if mutation == "missing"
        else inputs[1:]
        if mutation == "orphan"
        else (*inputs, inputs[-1])
    )
    with pytest.raises(ValueError, match="PROTOCOL_INVALID"):
        protocol_sources(inputs)


def test_local_rounds_preserve_completion_order_instead_of_sorting_random_activity_ids():
    first = evidence(1, ContextMessage(role="assistant", content="old limit 1000"))
    second = evidence(5, ContextMessage(role="assistant", content="new limit 750"))
    first = ActivityContextEvidence(
        first.ref.model_copy(update={"source_id": "activity:zz:0"}), first.message
    )
    second = ActivityContextEvidence(
        second.ref.model_copy(update={"source_id": "activity:aa:0"}), second.message
    )
    result = compile((*protocol_sources((second, first)), source(10, "question", protected=True)))
    contents = [message.content for message in result.manifest.messages]
    assert contents.index("old limit 1000") < contents.index("new limit 750")
