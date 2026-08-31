"""Cryptographic capabilities and restart-bound application values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

TokenKind = Literal["access", "refresh"]
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"


class TokenCodecError(ValueError):
    """A token cannot be authenticated or does not satisfy its expected kind."""


class SecretCipherError(ValueError):
    """A persisted secret envelope cannot be decrypted."""


@runtime_checkable
class PasswordHashPort(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password: str, password_hash: str | None) -> bool: ...


@runtime_checkable
class TokenCodecPort(Protocol):
    def issue_access_token(self, *, user_id: str, role: str, token_version: int) -> str: ...

    def issue_refresh_token(self, *, user_id: str, token_version: int) -> str: ...

    def decode(
        self,
        token: str,
        expected_type: TokenKind | None = None,
    ) -> dict[str, Any]: ...

    def hash_token(self, token: str) -> str: ...


class GeneratedServiceKey(Protocol):
    plaintext: str
    key_hash: str
    prefix: str


@runtime_checkable
class ServiceKeyPort(Protocol):
    def generate(self) -> GeneratedServiceKey: ...

    def hash(self, plaintext: str) -> str: ...


@runtime_checkable
class CookieManagerPort(Protocol):
    def set_auth_cookies(
        self,
        response: Any,
        *,
        access_token: str,
        refresh_token: str,
    ) -> str: ...

    def clear_auth_cookies(self, response: Any) -> None: ...


@runtime_checkable
class CsrfPort(Protocol):
    def verify_request(self, request: Any) -> None: ...


@runtime_checkable
class OAuthRegistryPort(Protocol):
    def get(self, provider: str) -> Any: ...
    def enabled_providers(self) -> list[str]: ...


@runtime_checkable
class VersionedSecretCipher(Protocol):
    current_key_id: str

    def encrypt_versioned(self, plain: str) -> str: ...

    def decrypt_versioned(self, encrypted: str) -> str: ...


@dataclass(frozen=True)
class EncryptedMapping:
    value: dict[str, Any] | None
    scheme: str


@dataclass(frozen=True)
class EncryptedValue:
    value: str | None
    scheme: str


@runtime_checkable
class SecretEnvelopePort(Protocol):
    def encrypt_mapping(self, value: dict[str, Any] | None) -> EncryptedMapping: ...

    def encrypt_url(self, value: str | None) -> EncryptedValue: ...


@dataclass(frozen=True)
class ApplicationUrls:
    frontend_base_url: str
    oauth_redirect_base: str = ""


@dataclass(frozen=True)
class BootstrapAdminCredentials:
    email: str
    password: str


@dataclass(frozen=True)
class OutboundNetworkPolicy:
    allowed_ports: frozenset[int]
    allow_private_hosts: frozenset[str] = frozenset()
