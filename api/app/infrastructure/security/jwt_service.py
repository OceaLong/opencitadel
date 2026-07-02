#!/usr/bin/env python
# -*- coding: utf-8 -*-
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal

import jwt


TokenKind = Literal["access", "refresh"]


class JwtService:
    def __init__(
            self,
            secret: str,
            access_ttl_seconds: int = 900,
            refresh_ttl_seconds: int = 60 * 60 * 24 * 30,
            issuer: str = "opencitadel",
    ) -> None:
        self.secret = secret
        self.access_ttl_seconds = access_ttl_seconds
        self.refresh_ttl_seconds = refresh_ttl_seconds
        self.issuer = issuer

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _encode(self, payload: Dict[str, Any], ttl_seconds: int, token_type: TokenKind) -> str:
        now = datetime.now(timezone.utc)
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

    def decode(self, token: str, expected_type: TokenKind | None = None) -> Dict[str, Any]:
        claims = jwt.decode(token, self.secret, algorithms=["HS256"], issuer=self.issuer)
        if expected_type is not None and claims.get("typ") != expected_type:
            raise jwt.InvalidTokenError("unexpected token type")
        return claims
