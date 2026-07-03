#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM API Key storage format identifiers for llm_endpoints.api_key_encryption."""

from enum import StrEnum


class ApiKeyEncryption(StrEnum):
    """How llm_endpoints.api_key is stored."""

    LEGACY_PLAINTEXT = "legacy_plaintext"
    FERNET_V1 = "fernet_v1"
