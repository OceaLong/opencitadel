"""Cryptographic capabilities and restart-bound application values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

TokenKind = Literal["access", "refresh"]
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"

# The `__Host-` prefix locks a cookie to the exact host over HTTPS: the browser
# only accepts it when it is Secure, has Path=/, and carries no Domain attribute.
# That is precisely what defeats the sibling-subdomain overwrite that would let a
# cousin origin shadow the CSRF double-submit cookie on a shared parent domain.
_HOST_COOKIE_PREFIX = "__Host-"


def _use_host_prefix(cookie_domain: str | None, cookie_secure: bool) -> bool:
    """`__Host-` is usable only when no cookie Domain is configured (the prefix
    forbids the Domain attribute) and cookies are Secure (the prefix requires it).
    In non-secure dev over http, or when a shared Domain is deliberately set, the
    bare base name is used so the write side stays valid."""
    return bool(cookie_secure) and not cookie_domain


def host_cookie_name(base_name: str, *, cookie_domain: str | None, cookie_secure: bool) -> str:
    """Central write-side name resolver. Returns the `__Host-`-prefixed name when
    the prefix is usable for the given deployment, otherwise the bare base name.
    The read side pairs with :func:`read_host_cookie`."""
    if _use_host_prefix(cookie_domain, cookie_secure):
        return f"{_HOST_COOKIE_PREFIX}{base_name}"
    return base_name


def read_host_cookie(cookies: Any, base_name: str) -> str | None:
    """Central read-side resolver that pairs with :func:`host_cookie_name` without
    needing the request-time cookie Domain/Secure flags (not every read point can
    reach them). The write side ever emits exactly one of the two names, so trying
    the `__Host-` name first and the bare name second always recovers it. Trying
    the prefixed name first is also the secure choice: a `__Host-` cookie can only
    have been set host-locked over HTTPS, so a sibling-subdomain-planted bare-name
    cookie can never shadow it."""
    prefixed = cookies.get(f"{_HOST_COOKIE_PREFIX}{base_name}")
    if prefixed is not None:
        return prefixed
    return cookies.get(base_name)


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
