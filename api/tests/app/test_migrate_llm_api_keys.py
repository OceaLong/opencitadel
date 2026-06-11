#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption
from app.infrastructure.security.llm_key_inspector import build_inspection_report


def test_migration_report_counts_only_legacy_plaintext_candidates():
    report = build_inspection_report([
        ("sk-old", ApiKeyEncryption.LEGACY_PLAINTEXT),
        ("", ApiKeyEncryption.LEGACY_PLAINTEXT),
        (ApiKeyCipher("f" * 32).encrypt("already"), ApiKeyEncryption.FERNET_V1),
    ])

    assert report.legacy_plaintext_count == 1
    assert report.empty_key_count == 1
    assert report.fernet_v1_count == 1


def test_inspection_log_lines_do_not_leak_secret_values():
    report = build_inspection_report([
        ("sk-old", ApiKeyEncryption.LEGACY_PLAINTEXT),
    ])
    joined = "\n".join(report.as_log_lines())
    assert "sk-old" not in joined
    assert "legacy_plaintext=1" in joined
