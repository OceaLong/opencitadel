#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Helpers for migrating llm_models rows into shared llm_endpoints."""
from __future__ import annotations

from typing import Iterable
from urllib.parse import urlparse

from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


def resolve_api_key_for_migration(stored: str, encryption: str, cipher: ApiKeyCipher) -> str:
    if not stored:
        return ""
    if encryption == ApiKeyEncryption.LEGACY_PLAINTEXT:
        return stored
    if encryption == ApiKeyEncryption.FERNET_V1:
        return cipher.decrypt_or_raise(stored)
    return stored


def endpoint_display_name(provider: str, base_url: str) -> str:
    try:
        host = urlparse(base_url).netloc or base_url
    except Exception:
        host = base_url
    return f"{provider} · {host}"[:255]


def group_model_rows(
        rows: Iterable[tuple],
        *,
        cipher: ApiKeyCipher,
) -> dict[tuple, dict]:
    """Group legacy llm_models rows by shared connection settings."""
    groups: dict[tuple, dict] = {}
    for row in rows:
        model_id, provider, base_url, api_key, encryption, owner_user_id, visibility, created_at = row
        decrypted_key = resolve_api_key_for_migration(
            api_key or "",
            encryption or ApiKeyEncryption.LEGACY_PLAINTEXT,
            cipher,
        )
        group_key = (
            provider,
            base_url or "",
            decrypted_key,
            owner_user_id or "",
            visibility or "global",
        )
        group = groups.setdefault(
            group_key,
            {
                "provider": provider,
                "base_url": base_url or "",
                "api_key": api_key or "",
                "encryption": encryption or ApiKeyEncryption.LEGACY_PLAINTEXT,
                "owner_user_id": owner_user_id,
                "visibility": visibility or "global",
                "created_at": created_at,
                "model_ids": [],
            },
        )
        group["model_ids"].append(model_id)
    return groups
