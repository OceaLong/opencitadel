#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.llm_model import LLMModel, LLMProvider
from app.infrastructure.external.llm.openai_llm import OpenAILLM


def test_openai_llm_exposes_supports_multimodal():
    llm = OpenAILLM(
        LLMModel(
            provider=LLMProvider.OPENAI,
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model_name="gpt-4o",
            supports_multimodal=True,
        )
    )
    assert llm.supports_multimodal is True
