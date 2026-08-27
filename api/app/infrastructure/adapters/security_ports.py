"""Infrastructure implementations of application cryptographic capabilities."""

from __future__ import annotations

from typing import Any

import jwt

from app.application.ports.crypto import (
    EncryptedMapping,
    EncryptedValue,
    SecretCipherError,
    SecretEnvelopePort,
    TokenCodecError,
    TokenCodecPort,
    TokenKind,
    VersionedSecretCipher,
)
from app.infrastructure.security.api_key_cipher import ApiKeyCipherError
from app.infrastructure.security.jwt_service import JwtService
from app.infrastructure.security.secret_dict_cipher import encrypt_secret_dict, encrypt_url


class JwtTokenCodecAdapter(TokenCodecPort):
    def __init__(self, codec: JwtService) -> None:
        self._codec = codec

    def issue_access_token(self, *, user_id: str, role: str, token_version: int) -> str:
        return self._codec.issue_access_token(
            user_id=user_id,
            role=role,
            token_version=token_version,
        )

    def issue_refresh_token(self, *, user_id: str, token_version: int) -> str:
        return self._codec.issue_refresh_token(
            user_id=user_id,
            token_version=token_version,
        )

    def decode(
        self,
        token: str,
        expected_type: TokenKind | None = None,
    ) -> dict[str, Any]:
        try:
            return self._codec.decode(token, expected_type=expected_type)
        except jwt.PyJWTError as exc:
            raise TokenCodecError("invalid token") from exc

    def hash_token(self, token: str) -> str:
        return self._codec.hash_token(token)


class FernetVersionedSecretCipherAdapter(VersionedSecretCipher):
    def __init__(self, cipher: VersionedSecretCipher) -> None:
        self._cipher = cipher

    @property
    def current_key_id(self) -> str:
        return self._cipher.current_key_id

    def encrypt_versioned(self, plain: str) -> str:
        return self._cipher.encrypt_versioned(plain)

    def decrypt_versioned(self, encrypted: str) -> str:
        try:
            return self._cipher.decrypt_versioned(encrypted)
        except ApiKeyCipherError as exc:
            raise SecretCipherError("secret cannot be decrypted") from exc


class FernetSecretEnvelopeAdapter(SecretEnvelopePort):
    def __init__(self, cipher: VersionedSecretCipher) -> None:
        self._cipher = cipher

    def encrypt_mapping(self, value: dict[str, Any] | None) -> EncryptedMapping:
        encrypted, scheme = encrypt_secret_dict(value, self._cipher)
        return EncryptedMapping(value=encrypted, scheme=scheme)

    def encrypt_url(self, value: str | None) -> EncryptedValue:
        encrypted, scheme = encrypt_url(value, self._cipher)
        return EncryptedValue(value=encrypted, scheme=scheme)
