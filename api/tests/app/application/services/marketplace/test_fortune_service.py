#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.services.marketplace.fortune_service import FortuneService


class FakeLLM:
    async def invoke(self, messages):
        return {
            "content": """
            {
              "title": "紫气东来",
              "summary": "近期运势温和向上。",
              "sections": [{"heading": "整体", "content": "宜稳中求进。"}],
              "lucky_items": {"color": "紫色", "number": "6", "keyword": "顺遂", "element": "火"},
              "disclaimer": "本结果仅供娱乐参考，请理性看待。"
            }
            """
        }


class FailingLLM:
    async def invoke(self, messages):
        raise RuntimeError("llm unavailable")


@pytest.fixture
def service():
    return FortuneService()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_generate_returns_structured_result(service):
    result = await service.generate(
        FakeLLM(),
        mode="fortune",
        question="近期事业运势如何？",
        input_profile={"nickname": "小明"},
    )
    assert result["title"] == "紫气东来"
    assert result["mode"] == "fortune"
    assert result["sections"][0]["heading"] == "整体"
    assert result["lucky_items"]["color"] == "紫色"


@pytest.mark.anyio
async def test_generate_falls_back_when_llm_fails(service):
    result = await service.generate(
        FailingLLM(),
        mode="lottery",
        question="抽一支签",
    )
    assert result["mode"] == "lottery"
    assert result["title"]
    assert result["sections"]


@pytest.mark.anyio
async def test_generate_rejects_invalid_mode(service):
    with pytest.raises(ValueError, match="不支持的预测类型"):
        await service.generate(FakeLLM(), mode="invalid", question="test")


@pytest.mark.anyio
async def test_generate_rejects_empty_question(service):
    with pytest.raises(ValueError, match="请输入你想预测的问题"):
        await service.generate(FakeLLM(), mode="fortune", question="   ")


class FakeStreamLLM:
    async def stream_invoke(self, messages):
        payload = (
            '{"title":"流式吉兆","summary":"流式生成成功。",'
            '"sections":[{"heading":"指引","content":"宜顺势而为。"}],'
            '"lucky_items":{"color":"蓝","number":"3","keyword":"顺","element":"水"},'
            '"disclaimer":"本结果仅供娱乐参考，请理性看待。"}'
        )
        for ch in payload:
            yield {"content": ch}


@pytest.mark.anyio
async def test_generate_stream_yields_delta_and_done(service):
    events = []
    async for event in service.generate_stream(
        FakeStreamLLM(),
        mode="fortune",
        question="近期运势如何？",
    ):
        events.append(event)

    assert any(e["type"] == "delta" for e in events)
    done = next(e for e in events if e["type"] == "done")
    assert done["result"]["title"] == "流式吉兆"
    assert done["result"]["mode"] == "fortune"
