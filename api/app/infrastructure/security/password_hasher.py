#!/usr/bin/env python
# -*- coding: utf-8 -*-
from passlib.context import CryptContext


class PasswordHasher:
    """Password hashing facade so services do not depend on passlib directly."""

    def __init__(self) -> None:
        self._context = CryptContext(schemes=["argon2"], deprecated="auto")

    def hash(self, password: str) -> str:
        return self._context.hash(password)

    def verify(self, password: str, password_hash: str | None) -> bool:
        if not password_hash:
            return False
        return self._context.verify(password, password_hash)
