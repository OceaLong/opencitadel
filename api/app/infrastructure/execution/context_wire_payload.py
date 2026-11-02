"""Compare the entire frozen context with its reviewed native transformation."""

import json

from app.infrastructure.external.llm.anthropic_wire import convert_messages, convert_tools


def context_payload_matches(provider, payload, messages, tools):
    if payload.get("functions"):
        return False
    if provider in {"openai", "azure"}:
        return (
            not payload.get("system")
            and payload.get("messages") == messages
            and (payload.get("tools") or []) == tools
        )
    if provider != "anthropic":
        raise ValueError("CONTEXT_PROVIDER_PROTOCOL_UNSUPPORTED")
    # The general native adapter tolerates invalid historical arguments. A
    # frozen context must never silently replace them with an empty object.
    for message in messages:
        for call in message.get("tool_calls") or []:
            arguments = (call.get("function") or {}).get("arguments")
            try:
                if not isinstance(json.loads(arguments), dict):
                    return False
            except (TypeError, ValueError):
                return False
    # The adapter may retry without prompt caching. Both exact wire forms
    # carry the same evidence and are independently charged by physical_send.
    for cached in (True, False):
        system, converted = convert_messages(messages, cache_system=cached)
        expected_tools = convert_tools(tools, cache_tools=cached)
        if (
            payload.get("messages") == converted
            and (payload.get("system") or "") == system
            and (payload.get("tools") or []) == expected_tools
        ):
            return True
    return False
