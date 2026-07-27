#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.infrastructure.migrations.llm_api_key_rotation import rotate_endpoint_records
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


def test_rotation_reencrypts_old_formats_and_is_idempotent():
    old_cipher = ApiKeyCipher("o" * 32, key_id="old")
    current_cipher = ApiKeyCipher(
        "n" * 32,
        key_id="current",
        previous_secrets={"old": "o" * 32},
    )
    records = [
        SimpleNamespace(
            api_key="sk-legacy",
            api_key_encryption=ApiKeyEncryption.LEGACY_PLAINTEXT,
        ),
        SimpleNamespace(
            api_key=old_cipher.encrypt("sk-v1"),
            api_key_encryption=ApiKeyEncryption.FERNET_V1,
        ),
        SimpleNamespace(
            api_key=old_cipher.encrypt_versioned("sk-old-v2"),
            api_key_encryption=ApiKeyEncryption.FERNET_V2,
        ),
        SimpleNamespace(
            api_key=current_cipher.encrypt_versioned("sk-current"),
            api_key_encryption=ApiKeyEncryption.FERNET_V2,
        ),
        SimpleNamespace(
            api_key="",
            api_key_encryption=ApiKeyEncryption.LEGACY_PLAINTEXT,
        ),
    ]

    first = rotate_endpoint_records(records, current_cipher)
    second = rotate_endpoint_records(records, current_cipher)

    assert first == {"rotated": 3, "unchanged": 1, "empty": 1}
    assert second == {"rotated": 0, "unchanged": 4, "empty": 1}
    assert all(
        record.api_key_encryption == ApiKeyEncryption.FERNET_V2
        for record in records[:-1]
    )
    assert all(
        current_cipher.key_id_from_ciphertext(record.api_key) == "current"
        for record in records[:-1]
    )
