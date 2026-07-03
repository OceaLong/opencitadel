#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.models.app_config import MCPTransport
from app.domain.models.integration_server import MCPServerRecord
from app.domain.utils.secret_masking import mask_string_value, mask_url
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption
from app.infrastructure.security.secret_dict_cipher import (
    decrypt_secret_dict,
    decrypt_url,
    encrypt_secret_dict,
    encrypt_url,
)


def test_encrypt_decrypt_secret_dict_roundtrip():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    original = {"Authorization": "Bearer secret-token", "X-Trace": "plain"}
    encrypted, enc_flag = encrypt_secret_dict(original, cipher)
    assert encrypted is not None
    assert encrypted["X-Trace"] == "plain"
    assert encrypted["Authorization"] != original["Authorization"]
    decrypted = decrypt_secret_dict(encrypted, enc_flag, cipher)
    assert decrypted == original


def test_encrypt_secret_dict_matches_access_key_hint():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    original = {"QINIU_ACCESS_KEY": "ak1234567890", "QINIU_BUCKET": "mybucket"}
    encrypted, enc_flag = encrypt_secret_dict(original, cipher)
    assert enc_flag == ApiKeyEncryption.FERNET_V1
    assert encrypted["QINIU_ACCESS_KEY"] != original["QINIU_ACCESS_KEY"]
    assert encrypted["QINIU_BUCKET"] == "mybucket"
    decrypted = decrypt_secret_dict(encrypted, enc_flag, cipher)
    assert decrypted == original


def test_encrypt_secret_dict_matches_exact_key_name():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    original = {"key": "secret-value"}
    encrypted, enc_flag = encrypt_secret_dict(original, cipher)
    assert enc_flag == ApiKeyEncryption.FERNET_V1
    decrypted = decrypt_secret_dict(encrypted, enc_flag, cipher)
    assert decrypted == original


def test_encrypt_url_with_query_roundtrip():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    original = "https://mcp.amap.com/mcp?key=3244242424"
    encrypted, enc_flag = encrypt_url(original, cipher)
    assert enc_flag == ApiKeyEncryption.FERNET_V1
    assert encrypted != original
    decrypted = decrypt_url(encrypted, enc_flag, cipher)
    assert decrypted == original


def test_encrypt_url_without_query_stays_plaintext():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    original = "https://example.com/mcp"
    encrypted, enc_flag = encrypt_url(original, cipher)
    assert enc_flag == ApiKeyEncryption.LEGACY_PLAINTEXT
    assert encrypted == original


def test_encrypt_url_with_userinfo_roundtrip():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    original = "https://user:supersecret@example.com/mcp"
    encrypted, enc_flag = encrypt_url(original, cipher)
    assert enc_flag == ApiKeyEncryption.FERNET_V1
    decrypted = decrypt_url(encrypted, enc_flag, cipher)
    assert decrypted == original


def test_mask_url_masks_query_values():
    masked = mask_url("https://mcp.amap.com/mcp?key=3244242424")
    assert masked == "https://mcp.amap.com/mcp?key=3244****2424"


def test_mask_url_masks_short_query_values():
    masked = mask_url("https://mcp.amap.com/mcp?key=324424")
    assert masked == "https://mcp.amap.com/mcp?key=****"


def test_mask_url_masks_userinfo_password():
    masked = mask_url("https://user:supersecret@example.com/mcp")
    assert masked == "https://user:supe****cret@example.com/mcp"


def test_mask_string_value_formats():
    assert mask_string_value("short") == "****"
    assert mask_string_value("1234567890") == "1234****7890"


def test_mcp_server_record_mask_secrets_includes_url():
    record = MCPServerRecord(
        id="1",
        name="amap",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://mcp.amap.com/mcp?key=3244242424",
        headers={"Authorization": "Bearer secret-token"},
        env={"QINIU_ACCESS_KEY": "ak1234567890"},
    )
    masked = record.mask_secrets()
    assert masked.url == "https://mcp.amap.com/mcp?key=3244****2424"
    assert masked.headers["Authorization"] == "Bear****oken"
    assert masked.env["QINIU_ACCESS_KEY"] == "ak12****7890"
