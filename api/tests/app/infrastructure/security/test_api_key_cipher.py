#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.infrastructure.security.api_key_cipher import ApiKeyCipher, ApiKeyCipherError


def test_encrypt_decrypt_roundtrip():
    cipher = ApiKeyCipher("a" * 32)
    encrypted = cipher.encrypt("sk-live-secret-key")
    assert encrypted != "sk-live-secret-key"
    assert cipher.decrypt_or_raise(encrypted) == "sk-live-secret-key"
    assert ApiKeyCipher.looks_like_fernet_token(encrypted)


def test_decrypt_or_raise_fails_for_wrong_secret():
    cipher_a = ApiKeyCipher("a" * 32)
    cipher_b = ApiKeyCipher("b" * 32)
    encrypted = cipher_a.encrypt("sk-live-secret-key")

    with pytest.raises(ApiKeyCipherError):
        cipher_b.decrypt_or_raise(encrypted)


def test_looks_like_fernet_token_rejects_plaintext():
    assert not ApiKeyCipher.looks_like_fernet_token("sk-live-secret-key")
    assert not ApiKeyCipher.looks_like_fernet_token("")


def test_mask_hides_middle_of_key():
    assert ApiKeyCipher.mask("sk-abcdefghijklmnop") == "sk-a****mnop"


def test_versioned_ciphertext_uses_current_key_and_reads_previous_key():
    old_cipher = ApiKeyCipher("o" * 32, key_id="old")
    old_value = old_cipher.encrypt_versioned("sk-rotating")
    rotating_cipher = ApiKeyCipher(
        "n" * 32,
        key_id="current",
        previous_secrets={"old": "o" * 32},
    )

    assert rotating_cipher.decrypt_versioned(old_value) == "sk-rotating"
    assert rotating_cipher.key_id_from_ciphertext(old_value) == "old"
    assert rotating_cipher.key_id_from_ciphertext(
        rotating_cipher.encrypt_versioned("sk-new")
    ) == "current"


def test_versioned_ciphertext_rejects_unknown_key_id():
    encrypted = ApiKeyCipher(
        "o" * 32,
        key_id="retired",
    ).encrypt_versioned("sk-retired")

    with pytest.raises(ApiKeyCipherError, match="key id"):
        ApiKeyCipher("n" * 32, key_id="current").decrypt_versioned(encrypted)
