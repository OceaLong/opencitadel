#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.services.llm_config_seed import is_seedable_llm_config, is_seedable_llm_config_raw
from app.domain.models.app_config import LLMConfig
from app.domain.models.session import Session
from app.infrastructure.external.llm.openai_llm import (
    _merge_thinking_request_kwargs,
    _resolve_request_model,
)


def test_resolve_request_model_reasoner_with_tools():
    extra = {}
    assert _resolve_request_model("deepseek-reasoner", [{"type": "function"}], extra) == "deepseek-chat"


def test_resolve_request_model_reasoner_without_tools():
    assert _resolve_request_model("deepseek-reasoner", None, {}) == "deepseek-reasoner"


def test_resolve_request_model_thinking_model_without_tools():
    extra = {"thinking_model_name": "deepseek-reasoner"}
    assert _resolve_request_model(
        "deepseek-chat",
        None,
        extra,
        thinking_enabled=True,
    ) == "deepseek-reasoner"


def test_resolve_request_model_thinking_disabled_uses_base_model():
    extra = {"thinking_model_name": "deepseek-reasoner"}
    assert _resolve_request_model(
        "deepseek-chat",
        None,
        extra,
        thinking_enabled=False,
    ) == "deepseek-chat"


def test_resolve_request_model_custom_tool_model():
    extra = {"tool_model_name": "deepseek-chat-v2"}
    assert _resolve_request_model("deepseek-reasoner", [{}], extra) == "deepseek-chat-v2"


def test_merge_thinking_request_kwargs_when_enabled():
    request_kwargs = {"model": "deepseek-chat"}
    extra = {
        "thinking_request_params": {"enable_thinking": True},
        "thinking_extra_body": {"thinking": {"type": "enabled"}},
    }
    _merge_thinking_request_kwargs(request_kwargs, extra, thinking_enabled=True)
    assert request_kwargs["enable_thinking"] is True
    assert request_kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


def test_merge_thinking_request_kwargs_when_disabled():
    request_kwargs = {"model": "deepseek-chat"}
    extra = {"thinking_request_params": {"enable_thinking": True}}
    _merge_thinking_request_kwargs(request_kwargs, extra, thinking_enabled=False)
    assert "enable_thinking" not in request_kwargs


def test_session_thinking_enabled_default_false():
    session = Session(title="test")
    assert session.thinking_enabled is False


def test_is_seedable_llm_config_raw_requires_explicit_section():
    assert is_seedable_llm_config_raw({}) is False
    assert is_seedable_llm_config_raw({"llm_config": {"model_name": "m"}}) is False
    assert is_seedable_llm_config_raw({
        "llm_config": {
            "base_url": "https://api.deepseek.com",
            "model_name": "deepseek-chat",
            "api_key": "sk-test",
        }
    }) is True


def test_is_seedable_llm_config_ollama_without_api_key():
    llm = LLMConfig(
        base_url="http://localhost:11434/v1",
        model_name="llama3",
        api_key="",
    )
    assert is_seedable_llm_config(llm) is True
