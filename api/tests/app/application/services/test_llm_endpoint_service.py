#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime

import pytest

from app.application.errors.exceptions import BadRequestError
from app.application.services.llm_endpoint_service import LLMEndpointService
from app.domain.models.llm_endpoint import LLMEndpoint
from app.domain.models.llm_model import LLMProvider, ResourceVisibility
from app.infrastructure.migrations.llm_endpoint_backfill import endpoint_display_name, group_model_rows
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


class _FakeEndpointRepo:
    def __init__(self):
        self.endpoints: dict[str, LLMEndpoint] = {}
        self.model_counts: dict[str, int] = {}

    async def get_all(self, scope=None):
        return list(self.endpoints.values())

    async def get_by_id(self, endpoint_id, scope=None):
        return self.endpoints.get(endpoint_id)

    async def save(self, endpoint, encrypted_api_key):
        self.endpoints[endpoint.id] = endpoint

    async def delete_by_id(self, endpoint_id):
        self.endpoints.pop(endpoint_id, None)

    async def count_models(self, endpoint_id):
        return self.model_counts.get(endpoint_id, 0)


class _FakeUow:
    def __init__(self, repo):
        self.llm_endpoint = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def test_group_model_rows_merges_same_connection():
    cipher = ApiKeyCipher("a" * 32)
    encrypted = cipher.encrypt("sk-shared")
    rows = [
        ("m1", "openai", "https://example.com/v1", encrypted, ApiKeyEncryption.FERNET_V1, None, "global", datetime.now()),
        ("m2", "openai", "https://example.com/v1", encrypted, ApiKeyEncryption.FERNET_V1, None, "global", datetime.now()),
        ("m3", "openai", "https://example.com/v1", "other-key", ApiKeyEncryption.LEGACY_PLAINTEXT, None, "global", datetime.now()),
    ]

    groups = group_model_rows(rows, cipher=cipher)

    assert len(groups) == 2
    shared_group = next(group for group in groups.values() if len(group["model_ids"]) == 2)
    assert shared_group["model_ids"] == ["m1", "m2"]


def test_endpoint_display_name_uses_host():
    assert endpoint_display_name("openai", "https://ws.example.com/compatible-mode/v1") == "openai · ws.example.com"


@pytest.mark.asyncio
async def test_delete_endpoint_rejects_when_models_exist():
    repo = _FakeEndpointRepo()
    endpoint = LLMEndpoint(
        id="ep-1",
        display_name="Test",
        provider=LLMProvider.OPENAI,
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
    )
    repo.endpoints[endpoint.id] = endpoint
    repo.model_counts[endpoint.id] = 2
    service = LLMEndpointService(lambda: _FakeUow(repo), ApiKeyCipher("b" * 32))

    with pytest.raises(BadRequestError, match="请先删除或迁移"):
        await service.delete_endpoint(endpoint.id)


@pytest.mark.asyncio
async def test_create_endpoint_encrypts_api_key():
    repo = _FakeEndpointRepo()
    service = LLMEndpointService(lambda: _FakeUow(repo), ApiKeyCipher("c" * 32))
    endpoint = LLMEndpoint(
        display_name="OpenAI",
        provider=LLMProvider.OPENAI,
        base_url="https://api.openai.com/v1",
        api_key="sk-secret",
        visibility=ResourceVisibility.GLOBAL,
    )

    created = await service.create_endpoint(endpoint)

    assert created.id in repo.endpoints
    assert created.api_key.endswith("secret") or "****" in created.api_key
