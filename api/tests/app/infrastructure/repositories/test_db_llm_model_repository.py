#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.infrastructure.repositories.db_llm_model_repository import DBLLMModelRepository
from app.infrastructure.security.api_key_cipher import ApiKeyCipher, ApiKeyCipherError
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


class _FakeRecord:
    def __init__(self, api_key: str, encryption: str):
        self.api_key = api_key
        self.api_key_encryption = encryption


def test_resolve_legacy_plaintext_without_decrypt():
    repo = DBLLMModelRepository(db_session=None, cipher=ApiKeyCipher("d" * 32))
    assert repo._resolve_api_key("sk-plain", ApiKeyEncryption.LEGACY_PLAINTEXT) == "sk-plain"


def test_resolve_fernet_v1_decrypts():
    cipher = ApiKeyCipher("d" * 32)
    repo = DBLLMModelRepository(db_session=None, cipher=cipher)
    encrypted = cipher.encrypt("sk-secret")
    assert repo._resolve_api_key(encrypted, ApiKeyEncryption.FERNET_V1) == "sk-secret"


def test_resolve_fernet_v1_raises_on_wrong_secret():
    cipher_a = ApiKeyCipher("d" * 32)
    cipher_b = ApiKeyCipher("e" * 32)
    encrypted = cipher_a.encrypt("sk-secret")
    repo = DBLLMModelRepository(db_session=None, cipher=cipher_b)

    with pytest.raises(ApiKeyCipherError):
        repo._resolve_api_key(encrypted, ApiKeyEncryption.FERNET_V1)
