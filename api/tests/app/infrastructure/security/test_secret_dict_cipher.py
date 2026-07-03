#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.secret_dict_cipher import decrypt_secret_dict, encrypt_secret_dict


def test_encrypt_decrypt_secret_dict_roundtrip():
    cipher = ApiKeyCipher("test-secret-key-for-unit-tests-only")
    original = {"Authorization": "Bearer secret-token", "X-Trace": "plain"}
    encrypted, enc_flag = encrypt_secret_dict(original, cipher)
    assert encrypted is not None
    assert encrypted["X-Trace"] == "plain"
    assert encrypted["Authorization"] != original["Authorization"]
    decrypted = decrypt_secret_dict(encrypted, enc_flag, cipher)
    assert decrypted == original
