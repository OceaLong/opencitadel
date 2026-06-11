#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption
from app.infrastructure.security.llm_key_inspector import build_inspection_report


def test_build_inspection_report_without_leaking_values():
    cipher = ApiKeyCipher("c" * 32)
    encrypted = cipher.encrypt("sk-secret")

    report = build_inspection_report([
        ("", ApiKeyEncryption.LEGACY_PLAINTEXT),
        ("sk-plain", ApiKeyEncryption.LEGACY_PLAINTEXT),
        (encrypted, ApiKeyEncryption.FERNET_V1),
        ("maybe-fernet", None),
    ])

    assert report.total_models == 4
    assert report.empty_key_count == 1
    assert report.legacy_plaintext_count == 1
    assert report.fernet_v1_count == 1
    assert report.unknown_encryption_count == 1
    assert report.suspected_plaintext_count == 1
    assert "sk-secret" not in "\n".join(report.as_log_lines())
    assert "sk-plain" not in "\n".join(report.as_log_lines())
