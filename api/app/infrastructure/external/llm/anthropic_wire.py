"""Pure native request transformation shared with context permit validation."""

import json
from typing import Any

from app.infrastructure.external.llm.base_llm import openai_content_to_anthropic_parts


def convert_messages(
    messages: list[dict[str, Any]], *, cache_system: bool = True
) -> tuple[Any, list[dict[str, Any]]]:
    system_parts = []
    converted = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            system_parts.append(content if isinstance(content, str) else json.dumps(content))
        elif role == "user":
            converted.append(
                {"role": "user", "content": openai_content_to_anthropic_parts(content)}
            )
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = content if isinstance(content, str) else ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tool_call in msg.get("tool_calls") or []:
                fn = tool_call.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    parsed_args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.get("id"),
                        "name": fn.get("name"),
                        "input": parsed_args or {},
                    }
                )
            converted.append(
                {
                    "role": "assistant",
                    "content": blocks or (content or ""),
                }
            )
        elif role == "tool":
            tool_content = content if isinstance(content, str) else json.dumps(content)
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id"),
                            "content": tool_content,
                        }
                    ],
                }
            )
    system_text = "\n".join(system_parts)
    if not system_text:
        return "", converted
    if not cache_system:
        return system_text, converted
    return [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ], converted


def convert_tools(tools: list[dict[str, Any]], *, cache_tools: bool = True) -> list[dict[str, Any]]:
    result = []
    for tool in tools or []:
        fn = tool.get("function", {})
        result.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description"),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    if cache_tools and result:
        result[-1]["cache_control"] = {"type": "ephemeral"}
    return result
