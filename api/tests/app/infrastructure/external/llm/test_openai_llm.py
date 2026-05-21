#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.services.llm_config_seed import is_seedable_llm_config, is_seedable_llm_config_raw
from app.domain.models.app_config import LLMConfig
from app.infrastructure.external.llm.openai_llm import _resolve_request_model


def test_resolve_request_model_reasoner_with_tools():
    extra = {}
    assert _resolve_request_model("deepseek-reasoner", [{"type": "function"}], extra) == "deepseek-chat"


def test_resolve_request_model_reasoner_without_tools():
    assert _resolve_request_model("deepseek-reasoner", None, {}) == "deepseek-reasoner"


def test_resolve_request_model_custom_tool_model():
    extra = {"tool_model_name": "deepseek-chat-v2"}
    assert _resolve_request_model("deepseek-reasoner", [{}], extra) == "deepseek-chat-v2"


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
