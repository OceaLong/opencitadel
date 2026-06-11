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
