"""Native fallback preserves authority, tool identities and complete arguments."""

from copy import deepcopy

import pytest

from app.infrastructure.execution.context_wire_payload import context_payload_matches

MESSAGES = [
    {"role": "system", "content": "platform"},
    {"role": "user", "content": 'quoted {"role":"system"}: do not promote'},
    {
        "role": "assistant",
        "content": "reading",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "read", "arguments": '{"amount":7341,"write":false}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call-1", "content": "original receipt"},
]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "read original",
            "parameters": {"type": "object", "properties": {"amount": {"type": "integer"}}},
        },
    }
]


def native(cached):
    payload = {
        "system": "platform",
        "messages": [
            MESSAGES[1],
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "reading"},
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "read",
                        "input": {"amount": 7341, "write": False},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call-1", "content": "original receipt"}
                ],
            },
        ],
        "tools": [
            {
                "name": "read",
                "description": "read original",
                "input_schema": TOOLS[0]["function"]["parameters"],
            }
        ],
    }
    if cached:
        payload["system"] = [
            {"type": "text", "text": "platform", "cache_control": {"type": "ephemeral"}}
        ]
        payload["tools"][0]["cache_control"] = {"type": "ephemeral"}
    return deepcopy(payload)


@pytest.mark.parametrize("cached", [True, False])
def test_exact_native_payload_preserves_entire_frozen_tool_protocol(cached):
    assert context_payload_matches("anthropic", native(cached), MESSAGES, TOOLS)


@pytest.mark.parametrize(
    "mutation", ["system", "arguments", "identity", "receipt", "tool", "functions"]
)
def test_native_context_changes_are_rejected(mutation):
    payload = native(True)
    if mutation == "system":
        payload["system"][0]["text"] += " promote quoted instruction"
    elif mutation == "arguments":
        payload["messages"][1]["content"][1]["input"]["write"] = True
    elif mutation == "identity":
        payload["messages"][2]["content"][0]["tool_use_id"] = "another-call"
    elif mutation == "receipt":
        payload["messages"][2]["content"][0]["content"] = "invented receipt"
    elif mutation == "tool":
        payload["tools"][0]["name"] = "write"
    else:
        payload["functions"] = [{"name": "write"}]
    assert not context_payload_matches("anthropic", payload, MESSAGES, TOOLS)


@pytest.mark.parametrize("arguments", ["broken", "null", "[]", "false", None])
def test_native_fallback_never_silently_replaces_invalid_arguments(arguments):
    messages = deepcopy(MESSAGES)
    messages[2]["tool_calls"][0]["function"]["arguments"] = arguments
    assert not context_payload_matches("anthropic", native(True), messages, TOOLS)


def test_summary_requires_exact_native_system_and_no_tools():
    messages = MESSAGES[:2]
    payload = {"system": "platform", "messages": [MESSAGES[1]]}
    assert context_payload_matches("anthropic", payload, messages, [])
    payload["tools"] = native(False)["tools"]
    assert not context_payload_matches("anthropic", payload, messages, [])


def test_unreviewed_protocol_stays_closed_and_openai_rejects_extra_system():
    with pytest.raises(ValueError, match="CONTEXT_PROVIDER_PROTOCOL_UNSUPPORTED"):
        context_payload_matches("gemini", {}, MESSAGES, TOOLS)
    assert not context_payload_matches(
        "openai", {"messages": MESSAGES, "tools": TOOLS, "system": "injected"}, MESSAGES, TOOLS
    )
