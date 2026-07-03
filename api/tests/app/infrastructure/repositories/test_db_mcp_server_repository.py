#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime

from app.domain.models.app_config import MCPTransport
from app.infrastructure.models.integration_server import MCPServerORM
from app.infrastructure.repositories.db_integration_server_repository import DBMCPServerRepository
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption
from app.infrastructure.security.secret_dict_cipher import encrypt_url


def test_to_domain_uses_decrypted_url_not_ciphertext():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    plaintext_url = "https://mcp.amap.com/mcp?key=3244242424"
    encrypted_url, url_encryption = encrypt_url(plaintext_url, cipher)
    assert url_encryption == ApiKeyEncryption.FERNET_V1
    assert encrypted_url != plaintext_url

    orm = MCPServerORM(
        id="mcp-1",
        name="amap-maps-streamableHTTP",
        transport=MCPTransport.STREAMABLE_HTTP.value,
        enabled=True,
        url=encrypted_url,
        url_encryption=url_encryption,
        visibility="global",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    repo = DBMCPServerRepository(db_session=None, cipher=cipher)  # type: ignore[arg-type]
    record = repo._to_domain(orm)

    assert record.url == plaintext_url
    assert record.url != encrypted_url
