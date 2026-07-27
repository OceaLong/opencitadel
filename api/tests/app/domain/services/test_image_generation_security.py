#!/usr/bin/env python
# -*- coding: utf-8 -*-
import base64
import json

import pytest

from app.domain.models.llm_model import LLMModel, LLMProvider
from app.domain.services import image_generation_service


class _Response:
    def __init__(self, payload: dict, *, content: bytes | None = None) -> None:
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload).encode()
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[tuple[str, str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url: str, **kwargs):
        self.requests.append(("POST", url))
        return self.response


@pytest.mark.asyncio
async def test_image_generation_uses_ssrf_safe_transport(monkeypatch):
    payload = {
        "data": [
            {
                "b64_json": base64.b64encode(b"safe-image").decode("ascii"),
            }
        ]
    }
    client = _Client(_Response(payload))
    factory_calls: list[dict] = []

    def safe_factory(**kwargs):
        factory_calls.append(kwargs)
        return client

    async def upload(*args, **kwargs):
        return "storage://generated-image"

    monkeypatch.setattr(
        image_generation_service,
        "create_ssrf_safe_async_client",
        safe_factory,
    )
    monkeypatch.setattr(
        image_generation_service,
        "upload_image_bytes_to_storage",
        upload,
    )
    model = LLMModel(
        provider=LLMProvider.OPENAI,
        base_url="https://images.example.test/v1",
        api_key="provider-secret",
    )

    result = await image_generation_service._generate_openai_image(
        "draw a citadel",
        model,
        object(),
        size="1024x1024",
        quality="standard",
        owner_user_id="user-1",
        team_id=None,
    )

    assert result == "storage://generated-image"
    assert factory_calls == [{"timeout": 120.0, "follow_redirects": False}]
    assert client.requests == [
        ("POST", "https://images.example.test/v1/images/generations")
    ]


@pytest.mark.asyncio
async def test_image_generation_rejects_oversized_provider_response(
    monkeypatch,
):
    payload = {
        "data": [
            {
                "b64_json": base64.b64encode(b"safe-image").decode("ascii"),
            }
        ]
    }
    client = _Client(
        _Response(
            payload,
            content=b"x" * (image_generation_service._MAX_PROVIDER_RESPONSE_BYTES + 1),
        )
    )
    uploaded = False

    def safe_factory(**kwargs):
        return client

    async def upload(*args, **kwargs):
        nonlocal uploaded
        uploaded = True
        return "storage://should-not-exist"

    monkeypatch.setattr(
        image_generation_service,
        "create_ssrf_safe_async_client",
        safe_factory,
    )
    monkeypatch.setattr(
        image_generation_service,
        "upload_image_bytes_to_storage",
        upload,
    )
    model = LLMModel(
        provider=LLMProvider.OPENAI,
        base_url="https://images.example.test/v1",
        api_key="provider-secret",
    )

    result = await image_generation_service._generate_openai_image(
        "draw a citadel",
        model,
        object(),
        size="1024x1024",
        quality="standard",
        owner_user_id="user-1",
        team_id=None,
    )

    assert result is None
    assert uploaded is False
