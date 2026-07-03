#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.application.services.integration_server_service import (
    _apply_masked_secret_updates,
    _merge_url_secrets,
    _should_keep,
)
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


def test_should_keep_empty_string():
    assert _should_keep("") is True
    assert _should_keep("   ") is True


def test_should_keep_masked_value():
    assert _should_keep("abcd****wxyz") is True


def test_should_keep_fernet_token():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    token = cipher.encrypt("secret-value")
    assert ApiKeyCipher.looks_like_fernet_token(token) is True
    assert _should_keep(token) is True


def test_should_keep_plain_new_value():
    assert _should_keep("new-key-value") is False


def test_merge_url_secrets_preserves_existing_when_masked():
    existing = "https://mcp.amap.com/mcp?key=3244242424"
    updated = "https://mcp.amap.com/mcp?key=3244****2424"
    assert _merge_url_secrets(updated, existing) == existing


def test_merge_url_secrets_preserves_existing_when_empty():
    existing = "https://mcp.amap.com/mcp?key=3244242424"
    assert _merge_url_secrets("", existing) == existing


def test_merge_url_secrets_preserves_existing_when_fernet_token():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    existing = "https://mcp.amap.com/mcp?key=3244242424"
    token = cipher.encrypt(existing)
    assert _merge_url_secrets(token, existing) == existing


def test_merge_url_secrets_uses_new_value_when_not_masked():
    existing = "https://mcp.amap.com/mcp?key=old-key-value"
    updated = "https://mcp.amap.com/mcp?key=new-key-value"
    assert _merge_url_secrets(updated, existing) == updated


def test_merge_url_secrets_merges_blank_param_with_existing():
    existing = "https://mcp.amap.com/mcp?key=secret-key&foo=bar"
    updated = "https://mcp.amap.com/mcp?key="
    assert _merge_url_secrets(updated, existing) == "https://mcp.amap.com/mcp?key=secret-key"


def test_merge_url_secrets_updates_service_url_but_keeps_blank_secret():
    existing = "https://old.example.com/mcp?key=secret-key"
    updated = "https://new.example.com/mcp?key="
    assert _merge_url_secrets(updated, existing) == "https://new.example.com/mcp?key=secret-key"


def test_merge_url_secrets_drops_removed_params():
    existing = "https://mcp.amap.com/mcp?key=secret-key&foo=bar"
    updated = "https://mcp.amap.com/mcp?key="
    assert "foo" not in (_merge_url_secrets(updated, existing) or "")


def test_merge_url_secrets_no_query_returns_updated_url():
    existing = "https://mcp.amap.com/mcp?key=secret"
    updated = "https://other.example.com/mcp"
    assert _merge_url_secrets(updated, existing) == updated


def test_apply_masked_secret_updates_per_key_with_masked():
    existing = {"API_KEY": "secret", "OTHER": "old"}
    updates = {"API_KEY": "****", "OTHER": "new"}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"API_KEY": "secret", "OTHER": "new"}


def test_apply_masked_secret_updates_blank_value_keeps_existing():
    existing = {"API_KEY": "secret", "OTHER": "old"}
    updates = {"API_KEY": "", "OTHER": "new"}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"API_KEY": "secret", "OTHER": "new"}


def test_apply_masked_secret_updates_fernet_token_keeps_existing():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    existing = {"API_KEY": "secret"}
    updates = {"API_KEY": cipher.encrypt("secret")}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"API_KEY": "secret"}


def test_apply_masked_secret_updates_drops_removed_keys():
    existing = {"API_KEY": "secret", "OTHER": "old"}
    updates = {"OTHER": "new"}
    result = _apply_masked_secret_updates(updates, existing)
    assert result == {"OTHER": "new"}
