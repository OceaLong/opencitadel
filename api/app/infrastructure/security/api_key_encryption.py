"""Inference credential storage format identifiers."""

from enum import StrEnum


class ApiKeyEncryption(StrEnum):
    """How inference endpoint credentials are stored."""

    PLAINTEXT = "plaintext"
    FERNET_V2 = "fernet_v2"
