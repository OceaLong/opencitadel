#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Pure credential-rotation helpers with secret-free result metadata."""
from typing import Iterable

from app.infrastructure.security.api_key_cipher import (
    ApiKeyCipher,
    ApiKeyCipherError,
)
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


def rotate_endpoint_records(
    records: Iterable[object],
    cipher: ApiKeyCipher,
) -> dict[str, int]:
    result = {"rotated": 0, "unchanged": 0, "empty": 0}
    for record in records:
        stored = str(getattr(record, "api_key", "") or "")
        encryption = getattr(
            record,
            "api_key_encryption",
            ApiKeyEncryption.LEGACY_PLAINTEXT,
        )
        if not stored:
            result["empty"] += 1
            continue

        if encryption == ApiKeyEncryption.FERNET_V2:
            if (
                cipher.key_id_from_ciphertext(stored)
                == cipher.current_key_id
            ):
                # Verify before declaring a credential current so corrupted
                # ciphertext cannot silently survive rotation.
                cipher.decrypt_versioned(stored)
                result["unchanged"] += 1
                continue
            plaintext = cipher.decrypt_versioned(stored)
        elif encryption == ApiKeyEncryption.FERNET_V1:
            plaintext = cipher.decrypt_or_raise(stored)
        elif encryption == ApiKeyEncryption.LEGACY_PLAINTEXT:
            plaintext = stored
        else:
            raise ApiKeyCipherError(
                f"未知的 API Key 加密格式: {encryption}"
            )

        record.api_key = cipher.encrypt_versioned(plaintext)
        record.api_key_encryption = ApiKeyEncryption.FERNET_V2
        result["rotated"] += 1
    return result
