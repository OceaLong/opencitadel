from app.domain.models.inference import ResolvedInferenceModel
from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM
from app.infrastructure.external.llm.gemini_llm import GeminiLLM
from tests.app.infrastructure.external.llm.inference_model_factory import (
    resolved_chat_model,
)


def _model() -> ResolvedInferenceModel:
    return resolved_chat_model(
        display_name="test",
        base_url="https://example.com",
        credential="sk-test",
        model_name="test-model",
    )


def test_anthropic_converts_assistant_tool_calls_to_tool_use_blocks():
    llm = AnthropicLLM(_model())
    _, converted = llm._convert_messages(
        [
            {
                "role": "assistant",
                "content": "calling tool",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"filepath": "/tmp/a.txt"}',
                        },
                    }
                ],
            },
        ]
    )
    assert converted[0]["role"] == "assistant"
    blocks = converted[0]["content"]
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "tool_use"
    assert blocks[1]["name"] == "read_file"
    assert blocks[1]["input"]["filepath"] == "/tmp/a.txt"


def test_gemini_converts_tools_and_parses_function_call_response():
    llm = GeminiLLM(_model())
    tools = llm._convert_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {
                        "type": "object",
                        "properties": {"filepath": {"type": "string"}},
                    },
                },
            }
        ]
    )
    assert tools[0]["name"] == "read_file"

    contents = llm._convert_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"filepath": "/tmp/a.txt"}',
                        },
                    }
                ],
            },
        ]
    )
    assert contents[0]["parts"][0]["functionCall"]["name"] == "read_file"
