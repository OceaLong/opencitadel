import hashlib
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

TokenKind = Literal["access", "refresh"]

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class JwtService:
    def __init__(
        self,
        secret: str,
        access_ttl_seconds: int = 900,
        refresh_ttl_seconds: int = 60 * 60 * 24 * 30,
        issuer: str = "opencitadel",
        *,
        previous_secrets: Mapping[str, str] | None = None,
    ) -> None:
        if not secret:
            raise ValueError("JWT secret 未配置，无法初始化 JwtService")
        self.secret = secret
        self.access_ttl_seconds = access_ttl_seconds
        self.refresh_ttl_seconds = refresh_ttl_seconds
        self.issuer = issuer
        # Tokens are always signed (encoded) with the current secret. On decode
        # the current secret is tried first, then each retired secret, so a key
        # rotation keeps sessions signed by a just-retired key valid until they
        # naturally expire instead of logging everyone out instantly. JWTs carry
        # no key id header here, so the secrets are simply tried in order.
        self._decode_secrets: list[str] = [secret]
        for previous_key_id, previous_secret in (previous_secrets or {}).items():
            if not _KEY_ID_RE.fullmatch(previous_key_id):
                raise ValueError(f"无效的历史 JWT key id: {previous_key_id}")
            if not previous_secret:
                raise ValueError(f"历史 JWT key id[{previous_key_id}]未配置密钥")
            if previous_secret != secret:
                self._decode_secrets.append(previous_secret)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _encode(self, payload: dict[str, Any], ttl_seconds: int, token_type: TokenKind) -> str:
        now = datetime.now(UTC)
        claims = {
            **payload,
            "typ": token_type,
            "iss": self.issuer,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
            "jti": secrets.token_urlsafe(16),
        }
        return jwt.encode(claims, self.secret, algorithm="HS256")

    def issue_access_token(self, *, user_id: str, role: str, token_version: int) -> str:
        return self._encode(
            {"sub": user_id, "role": role, "ver": token_version},
            self.access_ttl_seconds,
            "access",
        )

    def issue_refresh_token(self, *, user_id: str, token_version: int) -> str:
        return self._encode(
            {"sub": user_id, "ver": token_version},
            self.refresh_ttl_seconds,
            "refresh",
        )

    def decode(self, token: str, expected_type: TokenKind | None = None) -> dict[str, Any]:
        # Try the current secret first, then each retired one. Only a signature
        # mismatch falls through to the next secret; an otherwise-valid token
        # that is expired or has a bad issuer must surface that real error
        # rather than being masked by a signature failure on a different secret.
        signature_error: jwt.InvalidSignatureError | None = None
        for candidate in self._decode_secrets:
            try:
                claims = jwt.decode(token, candidate, algorithms=["HS256"], issuer=self.issuer)
            except jwt.InvalidSignatureError as exc:
                signature_error = exc
                continue
            if expected_type is not None and claims.get("typ") != expected_type:
                raise jwt.InvalidTokenError("unexpected token type")
            return claims
        raise (
            signature_error
            if signature_error is not None
            else jwt.InvalidTokenError("token could not be decoded")
        )
